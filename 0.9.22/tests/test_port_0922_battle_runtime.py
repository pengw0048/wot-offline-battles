import contextlib
import copy
import io
import math
from pathlib import Path
import pickle
import struct
import sys
import types
import unittest
from unittest import mock
import zlib


ROOT = Path(__file__).resolve().parents[2]
CLIENT_SCRIPTS = ROOT / '0.9.22' / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922.battle_runtime import (
    BattleRuntime, ENGINE_MODE_IDLE, ENGINE_MODE_OFF, ENGINE_MODE_RUNNING,
    FRAME_SECONDS, _FrameDiagnostics, _LANInputSender,
    _MOVEMENT_BACKWARD, _MOVEMENT_ROTATE_LEFT, _MOVEMENT_ROTATE_RIGHT,
    _engine_rotation,
    _selected_vehicle_has_sixth_sense)
from gui.mods.offline_lan_0922 import battle_runtime as \
    battle_runtime_module
from gui.mods.offline_lan_0922 import bot_runtime, combat_rules, \
    critical_damage, equipment_mechanics, gun_mechanics, tank_collision, \
    vehicle_physics
from gui.mods.offline_lan_0922.entities.remote_vehicle import \
    RemoteVehicle, RemoteVehicleFactory, _RemoteFilter, \
    collide_vehicle_at_matrix
from gui.mods.offline_lan_0922.entities import remote_vehicle as \
    remote_vehicle_module
from gui.mods.offline_lan_0922.entities.bigworld_binding import \
    BigWorldVehicleBinding
from gui.mods.offline_lan_0922.entities.native_remote_vehicle import \
    NativeRemoteVehicleFactory, _NativeRemoteState


class _Vector(object):
    def __init__(self, x=0.0, y=0.0, z=0.0):
        if not isinstance(x, (int, float)):
            try:
                x, y, z = x[0], x[1], x[2]
            except (TypeError, IndexError):
                x, y, z = x.x, x.y, x.z
        self.x, self.y, self.z = float(x), float(y), float(z)

    def __getitem__(self, index):
        return (self.x, self.y, self.z)[index]

    def __add__(self, other):
        return _Vector(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other):
        return _Vector(self.x - other.x, self.y - other.y, self.z - other.z)

    def __neg__(self):
        return _Vector(-self.x, -self.y, -self.z)

    @property
    def length(self):
        return math.sqrt(self.x * self.x + self.y * self.y + self.z * self.z)

    def scale(self, value):
        return _Vector(self.x * value, self.y * value, self.z * value)

    def normalise(self):
        length = self.length
        if length:
            self.x /= length
            self.y /= length
            self.z /= length


class _ReadOnlyVector(object):
    """Match native #1513 vectors whose components reject assignment."""

    def __init__(self, x=0.0, y=0.0, z=0.0):
        object.__setattr__(self, 'x', float(x))
        object.__setattr__(self, 'y', float(y))
        object.__setattr__(self, 'z', float(z))

    def __setattr__(self, name, unused_value):
        raise RuntimeError('Operation is not allowed')


class _MatrixAnimation(object):
    """Math.MatrixAnimation: a MatrixProvider with keyframes and a time."""

    def __init__(self):
        self.keyframes = ()
        self.time = 0.0
        self.yaw = self.pitch = self.roll = 0.0
        self.translation = _Vector()


class _MatrixInverse(object):
    def __init__(self, source):
        self.source = source


class _MatrixProduct(object):
    def __init__(self):
        self.a = None
        self.b = None


class _Matrix(object):
    def __init__(self, other=None):
        self.yaw = getattr(other, 'yaw', 0.0)
        self.pitch = getattr(other, 'pitch', 0.0)
        self.roll = getattr(other, 'roll', 0.0)
        self.translation = _Vector(getattr(
            other, 'translation', _Vector()))
        self.axis = _Vector(getattr(other, 'axis', _Vector(0.0, 0.0, 1.0)))

    def applyToOrigin(self):
        return _Vector(self.translation)

    def applyToAxis(self, index):
        if index != 2:
            raise NotImplementedError('only the forward axis is modelled')
        return _Vector(self.axis)

    def setIdentity(self):
        self.yaw = self.pitch = self.roll = 0.0
        self.translation = _Vector()

    def set(self, other):
        self.yaw = getattr(other, 'yaw', 0.0)
        self.pitch = getattr(other, 'pitch', 0.0)
        self.roll = getattr(other, 'roll', 0.0)
        self.translation = _Vector(getattr(
            other, 'translation', _Vector()))
        self.axis = _Vector(getattr(
            other, 'axis', _Vector(0.0, 0.0, 1.0)))

    def setRotateYPR(self, value):
        self.yaw, self.pitch, self.roll = map(float, value)

    def setRotateY(self, value):
        self.yaw = float(value)

    def setRotateX(self, value):
        self.pitch = float(value)

    def setTranslate(self, value):
        self.translation = _Vector(value)

    def postMultiply(self, unused_other):
        return None

    def preMultiply(self, unused_other):
        return None

    def invert(self):
        self.translation = -self.translation

    def applyPoint(self, value):
        value = _Vector(value)
        return value + self.translation


class _YawMatrix(_Matrix):
    """Rigid yaw transform for visible-pose collision regression tests."""

    def invert(self):
        yaw = self.yaw
        translation = self.translation
        self.yaw = -yaw
        cosine = math.cos(self.yaw)
        sine = math.sin(self.yaw)
        x = -translation.x
        z = -translation.z
        self.translation = _Vector(
            cosine * x + sine * z, -translation.y,
            -sine * x + cosine * z)

    def applyPoint(self, value):
        value = _Vector(value)
        cosine = math.cos(self.yaw)
        sine = math.sin(self.yaw)
        return _Vector(
            cosine * value.x + sine * value.z + self.translation.x,
            value.y + self.translation.y,
            -sine * value.x + cosine * value.z + self.translation.z)


class _VehicleFilter(object):
    """BigWorld.WGVehicleFilter built by createVehicleFilter, owned by nobody.

    Its native implementation pointer stays NULL until an entity adopts the
    filter, and every method asserts on it. `MF_ASSERT_DEV FAILED: pFilter`
    reaches `abort()`, so this fake fails loudly instead of returning.
    """

    def __init__(self):
        self.movementInfo = object()
        self.pFilter = None

    def setTracksSpeed(self, *unused_args):
        raise AssertionError(
            'MF_ASSERT_DEV FAILED: pFilter -> abort(): '
            'py_wg_vehicle_filter.cpp(555)')


class _TrackScroll(object):
    """BigWorld.PyTrackScroll: #1513 exposes all ten entries as methods."""

    def __init__(self):
        self.data = None
        self.active = False
        self.mode = None
        self.external = None
        self._left = 0.0
        self._right = 0.0
        # TrackScroller's constructor writes both contact flags true.
        self._contact = True

    def activate(self):
        self.active = True

    def deactivate(self):
        self.active = False

    def setData(self, value):
        self.data = value

    def setMode(self, mode):
        self.mode = mode

    def setExternal(self, left, right):
        self.external = (left, right)
        # The 20 Hz updater returns at once while the filter it was given has
        # no implementation, which is every filter no entity owns.
        if self.data is None or getattr(self.data, 'pFilter', None) is None:
            return
        if self.mode and self.mode[0] > 1:
            self._left += left
            self._right += right
            self._contact = True

    def leftScroll(self):
        return self._left

    def rightScroll(self):
        return self._right

    def leftContact(self):
        return self._contact

    def rightContact(self):
        return self._contact


class _Model(object):
    _SUPPORTED_ATTRIBUTES = frozenset((
        'matrix', 'visible', 'node_bindings', 'fashions'))

    def __init__(self):
        self.matrix = None
        self.visible = True
        self.node_bindings = []
        self.fashions = None

    def setupFashions(self, fashions):
        self.fashions = fashions

    def __setattr__(self, name, value):
        if name not in self._SUPPORTED_ATTRIBUTES:
            raise AttributeError(
                'PyCompoundModel has no %s attribute' % name)
        object.__setattr__(self, name, value)

    def node(self, unused_name, matrix_provider=None):
        if matrix_provider is not None:
            self.node_bindings.append((unused_name, matrix_provider))
        position = getattr(self.matrix, 'translation', _Vector())
        return types.SimpleNamespace(translation=_Vector(
            position.x, position.y + 1.5, position.z))


class _Signal(object):
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def __isub__(self, handler):
        self.handlers.remove(handler)
        return self


class _Watched(object):
    """Record the order of attribute writes reaching one native stand-in."""

    def __init__(self, target, label, order):
        object.__setattr__(self, '_target', target)
        object.__setattr__(self, '_label', label)
        object.__setattr__(self, '_order', order)

    def __getattr__(self, name):
        return getattr(object.__getattribute__(self, '_target'), name)

    def __setattr__(self, name, value):
        object.__getattribute__(self, '_order').append(
            (object.__getattribute__(self, '_label'), name, value is None))
        setattr(object.__getattribute__(self, '_target'), name, value)


class _FireExtra(object):
    """helpers.EntityExtra plus the vehicle_extras.Fire guards of #1513."""

    def __init__(self, log=None, index=22):
        self.index = index
        self.name = 'fire'
        self.log = [] if log is None else log

    def isRunningFor(self, entity):
        return self.index in entity.extras

    def startFor(self, entity, args=None):
        if self.index in entity.extras:
            raise Exception("the extra 'fire' is already started")
        data = {'extra': self, 'entity': entity}
        entity.extras[self.index] = data
        try:
            self._start(data)
        except Exception:
            del entity.extras[self.index]
            data['entity'] = None
            raise

    def stopFor(self, entity):
        data = entity.extras.pop(self.index, None)
        if data is None:
            return False
        self._cleanup(data)
        data['entity'] = None
        return True

    def stop(self, data):
        assert data['extra'] is self
        if data['entity'] is None:
            return
        del data['entity'].extras[self.index]
        self._cleanup(data)
        data['entity'] = None

    def _start(self, data):
        vehicle = data['entity']
        appearance = vehicle.appearance
        if not appearance.isUnderwater:
            data['_effectsPlayer'] = appearance.boundEffects.addNew(
                None, ('flaming',), ('start',), True, **data)
        appearance.switchFireVibrations(True)
        self.log.append(('fire start', vehicle.model))

    def _cleanup(self, data):
        vehicle = data['entity']
        vehicle.appearance.switchFireVibrations(False)
        self.log.append(('fire cleanup', vehicle.model))
        player = data.pop('_effectsPlayer', None)
        if player is None:
            return
        if vehicle.health <= 0:
            player.stop(forceCallback=True)
        else:
            player.keyOff()


def _bound_effects_modules(log):
    """Stand in for helpers.bound_effects with its exact #1513 ownership."""

    class ModelBoundEffects(object):
        def __init__(self, model):
            self.model = model
            self._effects = []

        def addNew(self, matProv, effectsList, keyPoints, waitForKeyOff,
                   **args):
            model = self.model
            effects = self._effects
            player = types.SimpleNamespace(model=model)

            def stop(keepPosteffects=False, forceCallback=False):
                log.append(('effect stop', model))
                if forceCallback and player in effects:
                    effects.remove(player)

            player.stop = stop
            player.keyOff = lambda: log.append(('effect key off', model))
            effects.append(player)
            log.append(('effect play', model))
            return player

        def stop(self):
            for player in tuple(self._effects):
                player.stop()
                self._effects.remove(player)

        def destroy(self):
            self.stop()
            self.model = None

    module = types.ModuleType('helpers.bound_effects')
    module.ModelBoundEffects = ModelBoundEffects
    package = types.ModuleType('helpers')
    package.bound_effects = module
    return {'helpers': package, 'helpers.bound_effects': module}


class _Strict1513Component(object):
    """Attribute-only stand-in for #1513's ``NoLegacyStuff`` mixin."""

    def __init__(self, **values):
        self.__dict__.update(values)

    def _forbidden(self, *unused_args, **unused_kwargs):
        raise AssertionError('Operation is not allowed')

    get = _forbidden
    __contains__ = _forbidden
    __getitem__ = _forbidden
    __iter__ = _forbidden
    items = _forbidden
    keys = _forbidden
    values = _forbidden


class _HitTester1513(object):
    def __init__(self, minimum, maximum, loaded=True):
        self._bounds = (minimum, maximum, None)
        self.bbox = self._bounds if loaded else None
        self.load_calls = 0
        self.release_calls = 0

    def loadBspModel(self):
        self.load_calls += 1
        if self.bbox is None:
            self.bbox = self._bounds

    def releaseBspModel(self):
        self.release_calls += 1
        if hasattr(self, 'bbox'):
            del self.bbox

    def localHitTest(self, unused_start, unused_end):
        return ()


class _Consumables(object):
    """#1513 ``_VehicleConsumables``: empty slots read back as ``default``."""

    def __init__(self, installed):
        self._items = [None if not intCD else types.SimpleNamespace(intCD=intCD)
                       for intCD in installed]

    def getIntCDs(self, default):
        return [default if item is None else item.intCD
                for item in self._items]

    def getInstalledItems(self):
        return [item for item in self._items if item is not None]


def _two_shell_descriptor():
    shots = [types.SimpleNamespace(shell=types.SimpleNamespace(
        compactDescr=compact_descr)) for compact_descr in (101, 102)]
    gun = types.SimpleNamespace(
        shots=shots, maxAmmo=40, clip=(1, 1.0), reloadTime=6.0,
        aimingTime=2.0, shotDispersionAngle=0.12,
        shotDispersionFactors={'afterShot': 1.5, 'turretRotation': 0.3})
    return types.SimpleNamespace(
        gun=gun, turret=types.SimpleNamespace(maxAmmo=40),
        chassis={'shotDispersionFactors': (0.2, 0.4)},
        activeGunShotIndex=0)


class _Descriptor(object):
    def __init__(self, name='ussr:R11_MS-1', loaded=True):
        self.name = name
        shell = types.SimpleNamespace(
            compactDescr=101, damage=(100.0, 50.0), caliber=37.0,
            kind='ARMOR_PIERCING', effectsIndex=3,
            explosionRadius=0.0)
        shot = types.SimpleNamespace(
            shell=shell, piercingPower=(1000.0, 800.0),
            speed=800.0, gravity=9.81, maxDistance=500.0)
        self.gun = types.SimpleNamespace(
            itemTypeName='vehicleGun',
            pitchLimits={'absolute': (-0.2, 0.4)}, shots=[shot],
            maxAmmo=40, clip=(1,), reloadTime=1.5, rotationSpeed=1.0,
            aimingTime=1.0, burst=(1, 0.1),
            shotDispersionAngle=0.0037,
            shotDispersionFactors={
                'afterShot': 4.0, 'turretRotation': 0.1},
            hitTester=_HitTester1513(
                _Vector(-0.2, -0.2, -0.5),
                _Vector(0.2, 0.2, 2.0), loaded))
        self.turret = types.SimpleNamespace(
            itemTypeName='vehicleTurret',
            circularVisionRadius=330.0, rotationSpeed=1.0,
            gunPosition=_Vector(0.0, 0.9, 0.0),
            hitTester=_HitTester1513(
                _Vector(-1.0, -0.4, -1.0),
                _Vector(1.0, 0.8, 1.0), loaded))
        self.radio = types.SimpleNamespace(distance=400.0)
        self.physics = {'speedLimits': (14.0, 7.0)}
        self.type = types.SimpleNamespace(name=name, tags=('lightTank',))
        self.chassis = _Strict1513Component(
            itemTypeName='vehicleChassis',
            hitTester=_HitTester1513(
                _Vector(-1.5, -0.8, -3.5),
                _Vector(1.5, 0.8, 3.5), loaded),
            hullPosition=_Vector(0.0, 0.6, 0.0),
            rotationSpeed=0.75,
            shotDispersionFactors=(0.14, 0.14))
        self.hull = _Strict1513Component(
            itemTypeName='vehicleHull',
            hitTester=_HitTester1513(
                _Vector(-1.7, -0.2, -3.5),
                _Vector(1.7, 1.4, 3.5), loaded),
            turretPositions=(_Vector(),))
        self.maxHealth = 500
        self.activeGunShotIndex = 0
        self.activeTurretPosition = 0

    def makeCompactDescr(self):
        return self.name

    def getHitTesters(self):
        return (self.chassis.hitTester, self.hull.hitTester,
                self.turret.hitTester, self.gun.hitTester)


class _VehicleDescr(object):
    def __new__(cls, typeName=None, compactDescr=None):
        return _Descriptor(
            typeName or compactDescr or 'ussr:R11_MS-1', loaded=False)


class _Vehicle(object):
    def __init__(self, entity_id, descriptor, position, rotation, properties):
        self.id = entity_id
        self.typeDescriptor = descriptor
        self.position = position
        self.rotation = tuple(rotation)
        self.yaw = float(rotation[2])
        self.matrix = _Matrix()
        self.matrix.setRotateYPR(rotation)
        self.matrix.setTranslate(position)
        self.model = _Model()
        self.model.matrix = self.matrix
        self.publicInfo = properties.get('publicInfo', {'team': 0})
        self.engineMode = (0, 0)
        self.track_scrolls = []
        self.engine_modes = []
        self.aim_targets = []
        self.visibility_changes = []

        def change_visibility(visible):
            self.visibility_changes.append(bool(visible))
            self.model.visible = bool(visible)

        self.appearance = types.SimpleNamespace(
            compoundModel=self.model, turretMatrix=_Matrix(),
            gunMatrix=_Matrix(),
            waterSensor=None, isInWater=False, isUnderwater=False,
            onModelChanged=_Signal(),
            changeVisibility=change_visibility,
            _CompoundAppearance__trackScrollCtl=_TrackScroll(),
            setupGunMatrixTargets=lambda target:
                self.aim_targets.append(target),
            updateTracksScroll=lambda left, right:
                self.track_scrolls.append((left, right)),
            changeEngineMode=lambda mode, forceSwinging=False:
                self.engine_modes.append(tuple(mode)))
        self.health = properties['health']
        self.isCrewActive = True
        self.gunAnglesPacked = properties.get('gunAnglesPacked', 0)
        self.isStarted = True
        self.inWorld = True
        self.teleports = []
        self.speed = 0.0
        self.filter = types.SimpleNamespace(
            longitudinalSpeed=0.0, angularSpeed=0.0,
            notifyInputKeysDown=mock.Mock())
        self.ammo_bay_effects = []
        self.shows = []
        self.draw_pass_visible = True

    def show(self, visible):
        self.shows.append(bool(visible))
        self.draw_pass_visible = bool(visible)

    def teleport(self, position, rotation):
        self.position = position
        self.rotation = tuple(rotation)
        self.yaw = float(rotation[2])
        self.teleports.append((position, rotation))

    def getAimParams(self):
        return (0.0, 0.0)

    def getSpeed(self):
        return self.speed

    def showShooting(self, burst, is_predicted=False):
        self.last_shot = (burst, is_predicted)

    def showAmmoBayEffect(self, mode, fireball_volume,
                          projected_turret_speed):
        self.ammo_bay_effects.append(
            (mode, fireball_volume, projected_turret_speed))

    def set_gunAnglesPacked(self, previous):
        self.previous_gun_angles = previous

    def set_health(self, previous):
        self.previous_health = previous

    def set_isCrewActive(self, previous):
        self.previous_crew_active = previous

    def isAlive(self):
        return self.health > 0 and self.isCrewActive

    def onHealthChanged(self, health, attacker_id, reason_id):
        self.health_change = (health, attacker_id, reason_id)


class _Arena(object):
    def __init__(self, avatar):
        self._avatar = avatar

    def onTeamBasePointsUpdate(self, team, base_id, points, time_left,
                               invaders, capturing_stopped):
        self._avatar.base_points.append((
            team, base_id, points, time_left, invaders,
            capturing_stopped))

    def onTeamBaseCaptured(self, team, base_id):
        self._avatar.base_captured.append((team, base_id))


class _ArenaDataProvider(object):
    def __init__(self, avatar):
        self.avatar = avatar
        self.player_vehicle_id = 0
        self.refreshes = 0

    def isRequiredDataExists(self):
        if self.player_vehicle_id > 0:
            return True
        self.refreshes += 1
        self.player_vehicle_id = int(self.avatar.playerVehicleID)
        return self.player_vehicle_id > 0

    def getPlayerVehicleID(self, forceUpdate=True):
        # Exact #1513 only force-refreshes a None cache. ArenaDP is created
        # before the local Vehicle and therefore normally holds integer 0.
        if forceUpdate and self.player_vehicle_id is None:
            self.refreshes += 1
            self.player_vehicle_id = int(self.avatar.playerVehicleID)
        return self.player_vehicle_id

    def getVehicleInfo(self, vehicle_id):
        return types.SimpleNamespace(
            vehicleID=int(vehicle_id), team=self.avatar.team)


class _InputHandler(object):
    def __init__(self):
        self.started_periods = []
        self.gun_marker_flags = []
        self.client_markers = []
        self.server_markers = []
        self._AvatarInputHandler__ctrlModeName = 'arcade'
        self._AvatarInputHandler__curCtrl = types.SimpleNamespace(
            camera=_ArcadeCamera(),
            setGunMarkerFlag=lambda positive, bit:
                self.gun_marker_flags.append((positive, bit)))
        self.steadyVehicleMatrixCalculator = types.SimpleNamespace(
            _SteadyVehicleMatrixCalculator__outputMProv=
            types.SimpleNamespace(rotationSrc=object(),
                                  translationSrc=object()),
            _SteadyVehicleMatrixCalculator__stabilisedMProv=
            types.SimpleNamespace(target=object()))

    def _AvatarInputHandler__onArenaStarted(self, period):
        self.started_periods.append(period)

    def showGunMarker(self, flag):
        self.client_markers.append(bool(flag))

    def showGunMarker2(self, flag):
        self.server_markers.append(bool(flag))


class _ArcadeCamera(object):
    def __init__(self):
        self._vehicle_matrix = object()
        self.bindings = []
        self.direction_resets = 0

    @property
    def vehicleMProv(self):
        return self._vehicle_matrix

    @vehicleMProv.setter
    def vehicleMProv(self, value):
        self._vehicle_matrix = value
        self.bindings.append(value)

    def setToVehicleDirection(self):
        self.direction_resets += 1


class _ConsistentMatrices(object):
    def __init__(self):
        self.targets = []
        self.attachedVehicleMatrix = types.SimpleNamespace(target=None)

    def _ConsistentMatrices__setTarget(self, matrix, as_static):
        self.targets.append((matrix, as_static))
        self.attachedVehicleMatrix.target = matrix


class _AdaptiveMatrixProvider(object):
    """Strict stand-in for #1513 Math.WGAdaptiveMatrixProvider."""

    def __init__(self, target):
        self._target = None
        self.target = target

    @property
    def target(self):
        return self._target

    @target.setter
    def target(self, value):
        if not isinstance(value, _Matrix):
            raise TypeError('adaptive matrix target must be a Matrix')
        self._target = value


class _Avatar(object):
    def handleVehicleCollidedVehicle(
            self, veh_a, veh_b, hit_point, contact_time):
        return None

    def __init__(self):
        self._offlineLANInitComplete = True
        self._offlineLANPlayerReady = True
        self.spaceID = 7
        self.team = 1
        self.playerVehicleID = 0
        self.isGunLocked = True
        self.ownVehicleAuxPhysicsData = 0
        self.ownVehicleGear = 0
        self.arena_updates = []
        self.positions = []
        self.round_finished = []
        self.viewpoint_switches = []
        self.ammo_updates = []
        self.reload_updates = []
        self.attr_updates = []
        self.optional_devices = []
        self.targeting_updates = []
        self.gun_tracking_calls = []
        self.gun_marker_updates = []
        self.damage_info = []
        self.other_vehicle_devices = []
        self.hit_directions = []
        self.shot_results = []
        self.dispersion_queries = []
        self.battle_events = []
        self.responses = []
        self.misc_statuses = []
        self.cancelWaitingForShot = mock.Mock()
        self.filter = object()
        self.base_points = []
        self.base_captured = []
        self.arena = _Arena(self)
        self.inputHandler = _InputHandler()
        self._PlayerAvatar__isOnArena = False
        self.consistentMatrices = _ConsistentMatrices()
        self._PlayerAvatar__ownVehicleStabMProv = \
            _AdaptiveMatrixProvider(_Matrix())
        self.visual_starts = []
        self.visual_stops = []
        self.gun_locks = []
        self.isGunLocked = False
        self.gunRotator = types.SimpleNamespace(
            turretYaw=0.0, gunPitch=0.0,
            dispersionAngle=0.25,
            turretRotationSpeed=0.5,
            _VehicleGunRotator__isStarted=False,
            _VehicleGunRotator__maxTurretRotationSpeed=None,
            _VehicleGunRotator__maxGunRotationSpeed=None,
            reset=mock.Mock(),
            lock=lambda locked: self.gun_locks.append(bool(locked)),
            getCurShotPosition=lambda: (
                _Vector(0.0, 2.0, 0.0), _Vector(0.0, 0.0, 1.0)))

        def start_gun_rotator():
            self.gun_tracking_calls.append('start')
            if (self._PlayerAvatar__isOnArena and
                    self.gunRotator.
                    _VehicleGunRotator__maxTurretRotationSpeed is not None):
                self.gunRotator._VehicleGunRotator__isStarted = True

        self.gunRotator.start = mock.Mock(side_effect=start_gun_rotator)
        self.terrainEffects = types.SimpleNamespace(addNew=mock.Mock())
        self.arena_dp = _ArenaDataProvider(self)
        self.view_points = types.SimpleNamespace(
            updateAttachedVehicle=mock.Mock())
        self.guiSessionProvider = types.SimpleNamespace(
            invalidateVehicleState=mock.Mock(),
            setVehicleHealth=mock.Mock(),
            getArenaDP=lambda: self.arena_dp,
            shared=types.SimpleNamespace(
                viewPoints=self.view_points,
                messages=types.SimpleNamespace(),
                feedback=types.SimpleNamespace(
                    _BattleFeedbackAdaptor__visible=set(),
                    setVehicleState=mock.Mock(),
                    invalidateStun=mock.Mock(),
                    showVehicleDamagedDevices=mock.Mock(),
                    hideVehicleDamagedDevices=mock.Mock()),
                vehicleState=types.SimpleNamespace()))
        self.vehicleTypeDescriptor = types.SimpleNamespace(
            extras=tuple(range(16)))

    def getOwnVehicleShotDispersionAngle(self, turret_rotation_speed,
                                         with_shot=0):
        self.dispersion_queries.append((turret_rotation_speed, with_shot))
        return [0.25, 0.125]

    def set_playerVehicleID(self, previous):
        self.previous_vehicle_id = previous

    def set_isGunLocked(self, previous):
        pass

    def set_ownVehicleAuxPhysicsData(self, previous):
        pass

    def set_ownVehicleGear(self, previous):
        pass

    def onVehicleChanged(self):
        self.vehicle_changed = getattr(self, 'vehicle_changed', 0) + 1

    def updateArena(self, kind, payload):
        self.arena_updates.append((kind, payload))

    def syncVehicleAttrs(self, values):
        self.synced_attrs = values
        self.attr_updates.append(dict(values))

    def updateVehicleOptionalDeviceStatus(self, vehicle_id, device_id, is_on):
        self.optional_devices.append((vehicle_id, device_id, is_on))

    def updateOwnVehiclePosition(self, position, direction,
                                 vehicle_speed, vehicle_rotation_speed):
        self.positions.append((position, direction, vehicle_speed,
                               vehicle_rotation_speed))

    def updateVehicleAmmo(self, vehicle_id, compact_descr, quantity,
                          quantity_in_clip, time_remaining):
        self.ammo_updates.append((vehicle_id, compact_descr, quantity,
                                  quantity_in_clip, time_remaining))

    def updateVehicleSetting(self, vehicle_id, code, value):
        self.last_setting = (vehicle_id, code, value)

    def updateTargetingInfo(self, turret_yaw, gun_pitch,
                            max_turret_rotation_speed,
                            max_gun_rotation_speed,
                            shot_disp_multiplier_factor,
                            gun_shot_dispersion_turret_rotation,
                            chassis_shot_dispersion_movement,
                            chassis_shot_dispersion_rotation, aiming_time):
        self.gun_tracking_calls.append('targeting')
        self.gunRotator._VehicleGunRotator__maxTurretRotationSpeed = \
            max_turret_rotation_speed
        self.gunRotator._VehicleGunRotator__maxGunRotationSpeed = \
            max_gun_rotation_speed
        self.targeting = (
            turret_yaw, gun_pitch, max_turret_rotation_speed,
            max_gun_rotation_speed, shot_disp_multiplier_factor,
            gun_shot_dispersion_turret_rotation,
            chassis_shot_dispersion_movement,
            chassis_shot_dispersion_rotation, aiming_time)
        self.targeting_updates.append(self.targeting)

    def updateGunMarker(self, vehicle_id, shot_position, shot_vector,
                        dispersion_angle):
        self.gun_marker_updates.append((
            vehicle_id, shot_position, shot_vector, dispersion_angle))

    def updateVehicleGunReloadTime(self, vehicle_id, time_left, base_time):
        self.reload = (vehicle_id, time_left, base_time)
        self.reload_updates.append(self.reload)

    def updateVehicleHealth(self, vehicle_id, health, death_reason_id,
                            is_crew_active, is_respawn):
        self.health_update = (vehicle_id, health, death_reason_id,
                              is_crew_active, is_respawn)

    def updateVehicleMiscStatus(self, vehicle_id, code, int_arg, float_args):
        self.misc_status = (vehicle_id, code, int_arg, float_args)
        self.misc_statuses.append(self.misc_status)

    def showVehicleDamageInfo(self, vehicle_id, damage_index, extra_index,
                              attacker_id, equipment_id):
        self.damage_info.append((vehicle_id, damage_index, extra_index,
                                 attacker_id, equipment_id))

    def showOtherVehicleDamagedDevices(
            self, vehicle_id, damaged_extras, destroyed_extras):
        self.other_vehicle_devices.append((
            vehicle_id, tuple(damaged_extras), tuple(destroyed_extras)))

    def showOwnVehicleHitDirection(self, hit_yaw, attacker_id, damage,
                                   crits, is_blocked, is_shell_he,
                                   damaged_id):
        self.hit_directions.append((
            hit_yaw, attacker_id, damage, crits, is_blocked,
            is_shell_he, damaged_id))

    def showShotResults(self, results):
        self.shot_results.append(list(results))

    def onBattleEvents(self, events):
        self.battle_events.append(list(events))

    def onRoundFinished(self, winner, reason):
        self.round_finished.append((winner, reason))

    def onCmdResponse(self, request_id, result_id, error):
        self.responses.append((request_id, result_id, error))

    def onSwitchViewpoint(self, vehicle_id, position):
        self.viewpoint_switches.append((vehicle_id, position))


class _Compatibility(object):
    def __init__(self):
        self.bridge = None
        self.configured = []
        self.hangar_space = None
        self.bigworld = None
        self.app_loader = None
        self.retired_players = set()
        self.disconnect_calls = 0
        self.network_client = None
        self.pose_overlays = {}
        self.control_mode_listener = None
        self.marker_player_vehicle_id = 0
        self.marker_damage_assertions = []
        self.target_lock_candidate = None
        self.target_lock_validations = []
        self.account_int_commands = []
        self.postmortem_vehicle_id = 0

    def dispatch_account_int_command(self, command, values):
        self.account_int_commands.append((command, values))
        return 0, ''

    def set_battle_network_client(self, client):
        self.network_client = client

    def set_control_mode_listener(self, listener):
        self.control_mode_listener = listener

    def synchronise_vehicle_marker_identity(self, vehicle_id):
        self.marker_player_vehicle_id = int(vehicle_id)
        return True

    def assert_vehicle_marker_identity(self, vehicle_id):
        if self.marker_player_vehicle_id != int(vehicle_id):
            raise RuntimeError('vehicle-marker player identity mismatch')
        return True

    def assert_vehicle_marker_damage_type(self, avatar, vehicle_id):
        self.assert_vehicle_marker_identity(vehicle_id)
        info = avatar.guiSessionProvider.getArenaDP().getVehicleInfo(
            int(vehicle_id))
        if int(info.vehicleID) != int(vehicle_id):
            raise RuntimeError('ArenaDP attacker identity mismatch')
        self.marker_damage_assertions.append((avatar, int(vehicle_id)))
        return True

    def set_target_lock_candidate(self, vehicle):
        self.target_lock_candidate = vehicle
        return True

    def set_postmortem_vehicle(self, vehicle_id):
        previous = self.postmortem_vehicle_id
        self.postmortem_vehicle_id = int(vehicle_id or 0)
        return previous

    def clear_postmortem_vehicle(self):
        previous = self.postmortem_vehicle_id
        self.postmortem_vehicle_id = 0
        return previous

    def validate_target_lock(self, avatar):
        self.target_lock_validations.append(avatar)
        return False

    def native_vehicle_attribute(self, vehicle, name):
        return getattr(vehicle, name)

    def set_vehicle_pose_overlay(self, vehicle, position, yaw, matrix,
                                 speed=0.0, turn_speed=0.0, velocity=None,
                                 acceleration=None,
                                 steady_rotation_matrix=None,
                                 stabilised_matrix=None):
        if steady_rotation_matrix is None:
            steady_rotation_matrix = matrix
        if stabilised_matrix is None:
            stabilised_matrix = matrix
        self.pose_overlays[id(vehicle)] = {
            'position': position, 'yaw': yaw, 'matrix': matrix,
            'speed': speed, 'turn_speed': turn_speed,
            'velocity': velocity, 'acceleration': acceleration,
            'steady_rotation_matrix': steady_rotation_matrix,
            'stabilised_matrix': stabilised_matrix}
        vehicle.position = position
        vehicle.yaw = yaw
        vehicle.matrix = matrix
        return True

    def clear_vehicle_pose_overlay(self, vehicle):
        return self.pose_overlays.pop(id(vehicle), None) is not None

    def bind_vehicle_pose_sources(self, avatar, vehicle):
        overlay = self.pose_overlays[id(vehicle)]
        matrix = overlay['matrix']
        steady_rotation = overlay['steady_rotation_matrix']
        stabilised_matrix = overlay['stabilised_matrix']
        avatar.consistentMatrices._ConsistentMatrices__setTarget(
            matrix, False)
        avatar._PlayerAvatar__ownVehicleStabMProv.target = stabilised_matrix
        calculator = avatar.inputHandler.steadyVehicleMatrixCalculator
        calculator._SteadyVehicleMatrixCalculator__outputMProv.rotationSrc = \
            steady_rotation
        calculator._SteadyVehicleMatrixCalculator__outputMProv.\
            translationSrc = stabilised_matrix
        calculator._SteadyVehicleMatrixCalculator__stabilisedMProv.target = \
            stabilised_matrix
        return True

    def restore_vehicle_pose_sources(self, avatar, vehicle, native_matrix,
                                     native_stabilised_matrix):
        unused_vehicle = vehicle
        avatar.consistentMatrices._ConsistentMatrices__setTarget(
            native_matrix, False)
        avatar._PlayerAvatar__ownVehicleStabMProv.target = \
            native_stabilised_matrix
        return True

    def configure_battle(self, gui_type, bonus_type, player_name=None,
                         player_team=None, arena_type_id=None):
        self.configured.append(
            (gui_type, bonus_type, player_name, player_team))
        self.arena_type_id = arena_type_id

    def attach_avatar_server(self, avatar, bridge):
        self.bridge = bridge

    def deactivate_map(self):
        self.deactivated = True

    def retire_current_player(self):
        if self.bigworld is None or self.bigworld.player() is None:
            return False
        player = self.bigworld.player()
        if player in self.retired_players:
            return False
        self.retired_players.add(player)
        if (self.hangar_space is not None and
                self.hangar_space.inited and
                self.hangar_space.spaceInited):
            self.bigworld.operations.append(('account_retire',))
            self.hangar_space.destroy()
        else:
            self.bigworld.operations.append(('avatar_retire',))
        return True

    def restore_lobby_account(self):
        self.account_restored = True
        if (self.bigworld is not None and
                self.bigworld.player() is not None and
                self.bigworld.player() not in self.retired_players):
            return self.bigworld.player()
        if self.hangar_space is not None:
            self.hangar_space.inited = True
            self.hangar_space.spaceInited = True
        account = _Avatar()
        if self.bigworld is not None:
            self.bigworld.avatar = account
        if self.app_loader is not None:
            self.app_loader.showLobby()
        return account

    def disconnect(self):
        self.disconnect_calls += 1
        if self.bigworld is not None:
            self.bigworld.operations.append(('offline_disconnect',))
            self.bigworld.avatar = None


class _AppLoader(object):
    __slots__ = (
        '__state', '__ctx', '__appFactory',
        'onGUISpaceLeft', 'onGUISpaceEntered', 'space_id',
        'actual_space_id', 'transitions', 'lobby_populates',
        'lobby_disposals', 'lobby_listener_balance')

    battle_page_calls = mock.Mock(return_value=True)
    battle_loading_calls = mock.Mock(return_value=True)
    lobby_callback = None

    def __init__(self):
        self._AppLoader__state = _AppState(self)
        self._AppLoader__ctx = None
        self._AppLoader__appFactory = None
        self.onGUISpaceLeft = None
        self.onGUISpaceEntered = None
        self.space_id = 4
        self.actual_space_id = 4
        self.transitions = []
        self.lobby_populates = 1
        self.lobby_disposals = 0
        self.lobby_listener_balance = 1

    def getSpaceID(self):
        return self.space_id

    def showBattleLoading(self):
        result = type(self).battle_loading_calls()
        self.transitions.append((self.actual_space_id, 5))
        # Match exact changeSpace(): ctx is mutated before the current state
        # accepts or rejects the requested transition.
        self.space_id = 5
        if result:
            self.lobby_disposals += 1
            self.lobby_listener_balance -= 1
            self.actual_space_id = 5
        return result

    def showBattlePage(self):
        result = type(self).battle_page_calls()
        self.transitions.append((self.actual_space_id, 6))
        self.space_id = 6
        if result:
            self.actual_space_id = 6
        return result

    def showLobby(self):
        callback = type(self).lobby_callback
        self.transitions.append((self.actual_space_id, 4))
        self.lobby_populates += 1
        self.lobby_listener_balance += 1
        self.space_id = 4
        self.actual_space_id = 4
        if callable(callback):
            return callback()
        return True


class _AppState(object):
    def __init__(self, loader):
        self.loader = loader

    def getSpaceID(self):
        return self.loader.actual_space_id


_APP_LOADER_SHOW_BATTLE_PAGE = _AppLoader.__dict__['showBattlePage']
_APP_LOADER_SHOW_BATTLE_LOADING = _AppLoader.__dict__['showBattleLoading']
_APP_LOADER_SHOW_LOBBY = _AppLoader.__dict__['showLobby']


class _ArenaLoadController(object):
    def __init__(self, app_loader):
        self.app_loader = app_loader
        self.invalidations = 0

    def invalidateArenaInfo(self):
        self.invalidations += 1
        return self.app_loader.showBattleLoading()


class _OfflineMap(object):
    def __init__(self, bigworld=None, app_loader=None):
        self.active = False
        self.bigworld = bigworld
        self.app_loader = app_loader
        self.viewer_camera_calls = 0

    def create(self, map_name):
        if self.app_loader is not None:
            self.app_loader.showBattlePage()
        if self.bigworld is not None:
            # Match exact #1513 OfflineMapCreator.create(): createSpace() does
            # not publish a BigWorld.spaces entry before geometry mapping.
            self.bigworld.setWatcher('Visibility/GUI', False)
            self.bigworld.addSpaceGeometryMapping(
                7, None, 'spaces/' + map_name)
            self.bigworld.operations.append(('map_create', map_name))
        self.active = True
        self.map_name = map_name
        if self.bigworld is not None and self.bigworld.avatar is None:
            self.bigworld.avatar = _Avatar()
        if self.bigworld is not None:
            avatar = self.bigworld.avatar
            self.bigworld.avatar.guiSessionProvider = types.SimpleNamespace(
                shared=types.SimpleNamespace(
                    arenaLoad=_ArenaLoadController(self.app_loader),
                    feedback=types.SimpleNamespace(
                        _BattleFeedbackAdaptor__visible=set(),
                        setVehicleState=mock.Mock()),
                    viewPoints=avatar.view_points),
                invalidateVehicleState=mock.Mock(),
                setVehicleHealth=mock.Mock(),
                getArenaDP=lambda: avatar.arena_dp,
                startVehicleVisual=lambda proxy, immediate:
                self.bigworld.avatar.visual_starts.append((proxy, immediate)),
                stopVehicleVisual=lambda entity_id, is_player:
                self.bigworld.avatar.visual_stops.append(
                    (entity_id, is_player)))
        self._OfflineMapCreator__setupCamera()

    def _OfflineMapCreator__setupCamera(self):
        self.viewer_camera_calls += 1

    def SetActive(self, active):
        self.active = bool(active)

    def Active(self):
        return self.active

    def destroy(self):
        self.active = False
        if self.bigworld is not None:
            self.bigworld.clearEntitiesAndSpaces()


class _HangarSpace(object):
    def __init__(self, operations):
        self.inited = True
        self.spaceInited = True
        self.operations = operations

    def destroy(self):
        self.operations.append(('hangar_destroy',))
        self.inited = False
        self.spaceInited = False


class _SpaceData(object):
    def __init__(self, operations, space_id, visibility_mask):
        self._operations = operations
        self._space_id = int(space_id)
        self._items_visibility_mask = visibility_mask

    @property
    def itemsVisibilityMask(self):
        return self._items_visibility_mask

    @itemsVisibilityMask.setter
    def itemsVisibilityMask(self, mask):
        self._items_visibility_mask = mask
        self._operations.append(
            ('space_visibility', self._space_id, mask))


def _compiled_space_binary(masks=(0xffffff89, 0xffffff82,
                                  0xffffff89, 0xffffff84)):
    records = bytearray()
    for index, mask in enumerate(masks):
        record = bytearray(124)
        struct.pack_into('<II', record, 68, index % 3, index + 1)
        struct.pack_into('<I', record, 120, mask)
        records.extend(record)
    section = struct.pack('<II', 124, len(masks)) + bytes(records)
    directory_end = 48
    return (struct.pack('<4s5I', b'BWTB', 1, directory_end, 0, 0, 1) +
            struct.pack('<4s5I', b'WTCP', 2, directory_end, 0,
                        len(section), 0) + section)


def _control_point_masks(binary):
    unused_magic, unused_version, unused_end, unused_a, unused_b, count = \
        struct.unpack_from('<4s5I', binary, 0)
    for index in range(count):
        row = struct.unpack_from('<4s5I', binary, 24 + index * 24)
        if row[0] != b'WTCP':
            continue
        record_size, record_count = struct.unpack_from('<II', binary, row[2])
        return [struct.unpack_from(
            '<I', binary, row[2] + 8 + item * record_size + 120)[0]
                for item in range(record_count)]
    return []


class _DataSection(object):
    def __init__(self, binary):
        self.asBinary = binary


class _ResMgr(object):
    def __init__(self, operations):
        self.operations = operations
        self.original = _compiled_space_binary()
        self.sections = {}
        self.purges = []

    def openSection(self, path):
        if path not in self.sections:
            self.sections[path] = _DataSection(self.original)
        return self.sections[path]

    def purge(self, path, recursive):
        self.operations.append(('purge', path, bool(recursive)))
        self.purges.append((path, bool(recursive)))
        self.sections.pop(path, None)


class _BigWorld(object):
    def __init__(self, avatar, compatibility):
        self.avatar = avatar
        self.compatibility = compatibility
        self.entities = {}
        self.callbacks = []
        self.operations = []
        self.now = 10.0
        self.space_status = 1.0
        self.next_id = 100
        self.defer_vehicle_entry = False
        self.reenter_vehicle_during_create = False
        self.pending_entities = {}
        self.created_offline_entities = []
        self.edge_adds = []
        self.edge_removes = []
        self.mouse_target = types.SimpleNamespace(
            translation=_Vector(), axis=_Vector(0.0, 0.0, 1.0))
        self.spaces = {
            7: _SpaceData(self.operations, 7, 0xffffffff)}
        self.pending_visibility_masks = {}
        self.space_data_factory = _SpaceData
        self.mapping_visibility_mask = 0xffffffff
        self.mapped_visibility_masks = []
        self.mapped_control_point_masks = []
        self.mapped_gui_visibility = []
        self.gui_visibility = True
        self.legacy_visibility_calls = []
        self.reset_visibility_before_ready = False
        self.res_mgr = None
        self.wg_collideWater = lambda *unused: None

    def player(self):
        return self.avatar

    def PyTrackScroll(self):
        return _TrackScroll()

    def time(self):
        return self.now

    def serverTime(self):
        return self.now

    def wg_getMatInfoNearPoint(self, unused_space_id, unused_start,
                               unused_end, unused_hit_point,
                               unused_filter):
        return (False, unused_hit_point, unused_hit_point,
                0, '', 0, 0)

    def callback(self, delay, function):
        if self.pending_entities and not self.defer_vehicle_entry:
            original = function

            def enter_pending_then_invoke():
                # Model the normal BigWorld lifecycle: createEntity returns
                # first, then Vehicle.onEnterWorld runs on an engine tick.
                for entity_id in list(self.pending_entities):
                    if entity_id in self.pending_entities:
                        self.enter_pending_vehicle(entity_id)
                return original()

            function = enter_pending_then_invoke
        self.callbacks.append(function)
        return len(self.callbacks)

    def cancelCallback(self, callback_id):
        pass

    def spaceLoadStatus(self):
        return self.space_status

    def createEntity(self, name, space_id, vehicle_id, position, rotation,
                     properties):
        self.next_id += 1
        if name == 'OfflineEntity':
            entity = types.SimpleNamespace(
                id=self.next_id, model=None, inWorld=True)
            self.entities[entity.id] = entity
            self.created_offline_entities.append({
                'id': entity.id, 'space_id': space_id,
                'position': position, 'rotation': rotation})
            return entity.id
        descriptor = _VehicleDescr(
            compactDescr=properties['publicInfo']['compDescr'])
        entity = _Vehicle(
            self.next_id, descriptor, position, rotation, properties)
        if self.reenter_vehicle_during_create:
            self._enter_vehicle(entity)
        else:
            self.pending_entities[entity.id] = entity
        return entity.id

    def _enter_vehicle(self, entity):
        bridge = self.compatibility.bridge
        if bridge is not None:
            bridge.prepareVehicleEnter(entity)
            # Exact #1513 CompoundAppearance.start owns the native Vehicle's
            # descriptor lifecycle and loads every BSP tester before the
            # entity becomes usable by frame-level collision consumers.
            for tester in entity.typeDescriptor.getHitTesters():
                tester.loadBspModel()
            bridge.acceptVehicleEnter(entity.id)
            if self.reset_visibility_before_ready:
                # Model exact #1513's late ClientVisibilityFlags update
                # before deferred client readiness is flushed.
                self.spaces[self.avatar.spaceID].itemsVisibilityMask = \
                    0x000fffff
            bridge.setClientReady()
            bridge.completeVehicleEnter(entity.id)
        # Match #1513: BigWorld.entity(id) becomes visible only after the
        # native vehicle_onEnterWorld callback has returned.
        self.entities[entity.id] = entity

    def enter_pending_vehicle(self, entity_id):
        entity = self.pending_entities.pop(entity_id)
        self._enter_vehicle(entity)

    def destroyEntity(self, entity_id):
        self.entities.pop(entity_id, None)

    def entity(self, entity_id):
        return self.entities.get(entity_id)

    def clearEntitiesAndSpaces(self):
        self.operations.append(('clear_entities_spaces',))
        self.entities.clear()
        self.pending_entities.clear()
        self.spaces.clear()
        self.pending_visibility_masks.clear()
        self.avatar = None

    def loadResourceListBG(self, assemblers, callback):
        descriptor = assemblers[0]
        callback({descriptor.name: _Model()})

    def setWatcher(self, name, enabled):
        self.operations.append(('watcher', name, enabled))
        if name == 'Visibility/GUI':
            self.gui_visibility = bool(enabled)

    def addSpaceGeometryMapping(self, space_id, unused_mapper, path):
        space_id = int(space_id)
        self.mapped_gui_visibility.append(self.gui_visibility)
        if self.res_mgr is not None:
            section = self.res_mgr.openSection(path + '/space.bin')
            self.mapped_control_point_masks.append([
                mask for mask in _control_point_masks(section.asBinary)
                if mask & self.mapping_visibility_mask])
        if space_id not in self.spaces:
            visibility_mask = 0xffffffff
            self.spaces[space_id] = self.space_data_factory(
                self.operations, space_id, visibility_mask)
        self.mapped_visibility_masks.append(
            self.mapping_visibility_mask)
        return 1

    def wg_getSpaceItemsVisibilityMask(self, space_id):
        space_id = int(space_id)
        self.legacy_visibility_calls.append(('get', space_id))
        if space_id in self.spaces:
            # Exact #1513 returns zero for this client-only PlayerAvatar
            # space even after its native setter was called successfully.
            return 0
        return self.pending_visibility_masks.get(space_id)

    def wg_setSpaceItemsVisibilityMask(self, space_id, mask):
        space_id = int(space_id)
        self.legacy_visibility_calls.append(('set', space_id, mask))
        # Exact #1513 export is inert for both mapped and unmapped spaces.
        return

    def clearAllSpaces(self):
        self.clearEntitiesAndSpaces()

    def wg_collideSegment(self, space_id, start, end, mask):
        if start.y > end.y and abs(start.x - end.x) < 0.001 and abs(start.z - end.z) < 0.001:
            return (_Vector(start.x, 0.0, start.z),)
        return None

    def MouseTargetingMatrix(self):
        return self.mouse_target

    def wgAddEdgeDetectEntity(self, entity, color, group, behind):
        self.edge_adds.append((entity, color, group, behind))

    def wgDelEdgeDetectEntity(self, entity):
        self.edge_removes.append(entity)


class _Client(object):
    def __init__(self):
        self.player_id = 1
        self.name = 'Player'
        self.vehicle = 'ussr:R11_MS-1'
        self.vehicle_compact_descr = 'dGVzdA=='
        self.team = 1
        self.slot = 0
        self.max_health = 500
        self.capabilities = (
            battle_runtime_module.lan_protocol.CLIENT_CAPABILITIES)
        self.server_capabilities = \
            battle_runtime_module.lan_protocol.CLIENT_CAPABILITIES
        self._input_seq = 0
        self._fire_intent_seq = 0
        self.sent = []

    def send_bot_manifest(self, bots):
        self.sent.append(('manifest', bots))
        return True

    def send_bot_state(self, bots):
        self.sent.append(('state', bots))
        return True

    def send_input(self, *values, **kwargs):
        self._input_seq += 1
        self.sent.append(('input', values, kwargs))
        return True

    def send_track_repair(self, tracks, base_revision, repair_seq):
        self.sent.append((
            'track_repair', tracks, base_revision, repair_seq))
        return True

    def send_fire_intent(self, shell_index, shot_origin, shot_direction,
                         dispersion_angle):
        self._fire_intent_seq += 1
        self.sent.append((
            'fire_intent', (shell_index,),
            {'input_seq': self._input_seq,
             'intent_seq': self._fire_intent_seq,
             'shot_origin': list(shot_origin),
             'shot_direction': list(shot_direction),
             'dispersion_angle': float(dispersion_angle)}))
        return self._fire_intent_seq


def _effective_params_snapshot(mass=25000.0, reload_factor=1.0,
                               ammo=None, deadeye=False):
    ammo_rows = [[101, 40]] if ammo is None else list(ammo)
    return {
        'version': 1,
        'loadout': {
            'crew_level': 100.0, 'commander_level': 100.0,
            'effective_crew_level': 100.0, 'crew_multiplier': 1.0,
            'crew_factor': 1.0, 'gun_rotation_factor': 1.0,
            'reload_factor': float(reload_factor),
            'aim_time_factor': 1.0, 'dispersion_factor': 1.0,
            'repair_factor': 1.0, 'vehicle_rotation_factor': 1.0,
            'radio_factor': 1.0,
            'bloom_move_factor': 1.0, 'bloom_rotation_factor': 1.0,
            'bloom_turret_factor': 1.0,
            'terrain_resistance_factors': [1.0, 1.0, 1.0],
            'has_big_kit': False, 'from_client_factors': True,
            'has_rammer': False, 'has_aim_drives': False,
            'has_ventilation': False, 'has_stabiliser': False,
            'has_rations': False, 'has_brotherhood': False,
            'has_snap_shot': False, 'has_smooth_ride': False,
            'has_sixth_sense': False,
        },
        'physics': {
            'mass': float(mass), 'powerW': 500000.0,
            'speedFwd': 14.0, 'speedBwd': 7.0, 'rotSpd': 0.75,
            'terrainResist': [1.0, 1.0, 1.0],
            'specificFriction': 1.0, 'brakeDecel': 4.0,
            'trackCenter': 2.0, 'minPlaneNormalY': 0.2,
            'nativePowerRatio': 1.0,
        },
        'spotting': {
            'commander_level': 100.0, 'recon_level': 0.0,
            'situational_level': 0.0, 'camouflage_level': 0.0,
            'binocular_factor': 1.0, 'binocular_delay': 3.0,
            'camouflage_net_bonus': 0.0, 'camouflage_net_delay': 3.0,
            'has_binoculars': False, 'has_camouflage_net': False,
            'vision_factor': 1.0, 'camouflage_factor': 0.57,
            'invisibility_moving': [0.0, 1.0],
            'invisibility_still': [0.0, 1.0],
            'from_client_factors': True,
        },
        'ramming': {'spall_coefficient': 1.0, 'ramming_bonus': 0.0},
        'ammo': ammo_rows,
        'camouflage': {
            'camouflage_id': None, 'base_moving': 0.171,
            'base_still': 0.228, 'shot_factor': 0.1,
        },
        'skills': {
            'deadeye': bool(deadeye), 'intuition_chances': 0,
        },
        'crew': {
            'members': [{
                'instance': 'commander', 'roles': ['commander'],
                'skills': [],
            }],
            'dynamic_spotting': {
                'crew': ['commander'],
                'states': dict(
                    ('%d:%d' % (mask, fire), {
                        'vision': 1.0, 'signal': 1.0,
                        'camouflage': 1.0,
                        'base_moving': 0.171,
                        'base_still': 0.228,
                        'invisibility_moving': [0.0, 1.0],
                        'invisibility_still': [0.0, 1.0],
                    })
                    for mask in (0, 1) for fire in (0, 1)),
            },
        },
        'equipment': [],
        'critical': {
            'devices': [{
                'name': 'engineHealth', 'max_hp': 100.0,
                'regen_hp': 50.0,
            }],
            'activation_targets': [],
            'crew_roster': ['commander'],
        },
        'gun': {
            'clip_size': 1,
            'shots': [{
                'compact_descr': int(row[0]),
                'source_shot': {
                    'speed': 800.0, 'gravity': 9.81,
                    'maxDistance': 500.0,
                    'piercingPower': [1000.0, 800.0],
                    'deadeye': bool(deadeye),
                    'shell': {
                        'kind': 'ARMOR_PIERCING', 'caliber': 37.0,
                        'damage': [100.0, 50.0],
                        'explosionRadius': 0.0,
                    },
                },
            } for row in ammo_rows],
        },
    }


def _human_gun_checkpoint(reload_time=0.0, clip=1, clip_size=1,
                          dispersion=0.02, reload_duration=5.0):
    return {
        'reload_time': float(reload_time),
        'reload_duration': float(reload_duration),
        'clip': int(clip), 'clip_size': int(clip_size),
        'dispersion': float(dispersion),
    }


def _minimal_start(round_id=1, map_name='01_karelia'):
    return {
        'round_id': round_id, 'map': map_name, 'bot_authority_id': -1,
        'players': [{
            'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
            'vehicle': 'ussr:R11_MS-1',
            'vehicle_compact_descr': 'dGVzdA==',
            'effective_params': _effective_params_snapshot(),
            'health': 500, 'max_health': 500, 'alive': True,
        }],
        'bots': [],
    }


def _mounted_current_vehicle_module():
    """Return the normal garage fixture used by visible-client starts."""
    current_vehicle = types.ModuleType('CurrentVehicle')
    current_vehicle.g_currentVehicle = types.SimpleNamespace(
        isPresent=lambda: True,
        item=types.SimpleNamespace(
            descriptor=_Descriptor('ussr:R11_MS-1'),
            shells=[types.SimpleNamespace(intCD=101, count=40)],
            equipment=None,
            crew=()))
    return current_vehicle


def _runtime():
    avatar = _Avatar()
    compatibility = _Compatibility()
    bigworld = _BigWorld(avatar, compatibility)
    res_mgr = _ResMgr(bigworld.operations)
    bigworld.res_mgr = res_mgr
    compatibility.bigworld = bigworld
    hangar_space = _HangarSpace(bigworld.operations)
    compatibility.hangar_space = hangar_space
    _AppLoader.showBattlePage = _APP_LOADER_SHOW_BATTLE_PAGE
    _AppLoader.showBattleLoading = _APP_LOADER_SHOW_BATTLE_LOADING
    _AppLoader.showLobby = _APP_LOADER_SHOW_LOBBY
    app_loader = _AppLoader()
    compatibility.app_loader = app_loader
    _AppLoader.battle_page_calls = mock.Mock(return_value=True)
    _AppLoader.battle_loading_calls = mock.Mock(return_value=True)
    _AppLoader.lobby_callback = None
    constants = types.SimpleNamespace(
        ARENA_GUI_TYPE=types.SimpleNamespace(RANDOM=1),
        ARENA_BONUS_TYPE=types.SimpleNamespace(REGULAR=2),
        ARENA_UPDATE=types.SimpleNamespace(
            VEHICLE_ADDED=2, PERIOD=3, VEHICLE_STATISTICS=5,
            VEHICLE_KILLED=6, AVATAR_READY=7, TEAM_KILLER=10),
        ARENA_PERIOD=types.SimpleNamespace(PREBATTLE=2, BATTLE=3),
        VEHICLE_PHYSICS_MODE=types.SimpleNamespace(STANDARD=0),
        VEHICLE_SIEGE_STATE=types.SimpleNamespace(
            DISABLED=0, SWITCHING_ON=1, ENABLED=2, SWITCHING_OFF=3),
        VEHICLE_SETTING=types.SimpleNamespace(
            CURRENT_SHELLS=0, NEXT_SHELLS=1, SIEGE_MODE_ENABLED=3,
            ACTIVATE_EQUIPMENT=16, RELOAD_PARTIAL_CLIP=17),
        VEHICLE_MISC_STATUS=types.SimpleNamespace(
            OTHER_VEHICLE_DAMAGED_DEVICES_VISIBLE=0,
            LOADER_INTUITION_WAS_USED=2, VEHICLE_DROWN_WARNING=4,
            VEHICLE_IS_OVERTURNED=3,
            SIEGE_MODE_STATE_CHANGED=9),
        DROWN_WARNING_LEVEL=types.SimpleNamespace(
            SAFE=0, CAUTION=1, DANGER=2),
        OVERTURN_WARNING_LEVEL=types.SimpleNamespace(
            SAFE=0, CAUTION=1, DANGER=2),
        OVERTURN_CONDITION=types.SimpleNamespace(
            IGNOR_DELAY=0.1,
            WARNING_COSINE=math.cos(math.radians(70.0)),
            ONBOARD_COSINE=math.cos(math.radians(80.0)),
            OVERTURN_COSINE=math.cos(math.radians(120.0)),
            HULL_PRESSURE=0.2),
        ATTACK_REASON=types.SimpleNamespace(
            SHOT='shot', FIRE='fire', RAM='ram',
            WORLD_COLLISION='world_collision',
            DEATH_ZONE='death_zone', DROWNING='drowning',
            OVERTURN='overturn'),
        ATTACK_REASON_INDICES={
            'shot': 0, 'fire': 1, 'ram': 2,
            'world_collision': 3, 'death_zone': 4, 'drowning': 5,
            'overturn': 7},
        AMMOBAY_DESTRUCTION_MODE=types.SimpleNamespace(
            POWDER_BURN_OFF=0, POWDER_EXPLOSION=1, HE_DETONATION=2),
        DAMAGE_INFO_INDICES={
            'DEVICE_DESTROYED_AT_FIRE': 10,
            'DEVICE_CRITICAL_AT_WORLD_COLLISION': 11,
            'TANKMAN_HIT_AT_DROWNING': 12,
            'FIRE_STOPPED': 13,
        },
        DAMAGE_INFO_CODES=tuple(
            'CODE_%d' % index for index in range(38)),
        EQUIPMENT_STAGES=types.SimpleNamespace(
            NOT_RUNNING=0, DEPLOYING=1, UNAVAILABLE=2, READY=3,
            PREPARING=4, ACTIVE=5, COOLDOWN=6, EXHAUSTED=255),
        VEHICLE_HIT_FLAGS=types.SimpleNamespace(
            VEHICLE_KILLED=1, FIRE_STARTED=4, RICOCHET=8,
            MATERIAL_WITH_POSITIVE_DF_PIERCED_BY_PROJECTILE=16,
            MATERIAL_WITH_POSITIVE_DF_NOT_PIERCED_BY_PROJECTILE=32,
            DEVICE_DAMAGED_BY_PROJECTILE=1024,
            CHASSIS_DAMAGED_BY_PROJECTILE=2048,
            GUN_DAMAGED_BY_PROJECTILE=4096,
            MATERIAL_WITH_POSITIVE_DF_PIERCED_BY_EXPLOSION=8192,
            DEVICE_DAMAGED_BY_EXPLOSION=65536,
            CHASSIS_DAMAGED_BY_EXPLOSION=131072,
            GUN_DAMAGED_BY_EXPLOSION=262144,
            ATTACK_IS_DIRECT_PROJECTILE=1048576,
            ATTACK_IS_EXTERNAL_EXPLOSION=2097152),
        FINISH_REASON=types.SimpleNamespace(
            EXTERMINATION=1, BASE=2, TIMEOUT=3, FAILURE=4, TECHNICAL=5))
    arena = types.SimpleNamespace(
        geometryName='01_karelia', gameplayName='ctf', gameplayID=0)
    width = 61
    graph_template = {
        'format': 'offline-lan-0922-navgraph', 'version': 2,
        'game_version': '0.9.22.0.1-cn-1513',
        'origin': (-120.0, -120.0), 'cell_size': 4.0,
        'bounds': (-120.0, -120.0, 120.0, 120.0),
        'width': width, 'height': width,
        'heights_mm': tuple([0] * (width * width)),
        'links': tuple([0] * (width * width)),
        'hazards': tuple([0] * (width * width)),
        'spawn_anchors': ((0.0, -40.0), (0.0, 40.0)),
        'objective_bases': ((0.0, 40.0), (0.0, -40.0)),
        'spawn_formations': {
            '1': tuple(((slot % 5 - 2) * 12.0, 0.0,
                        -80.0 + (slot // 5) * 12.0, 0.0)
                       for slot in range(15)),
            '2': tuple(((slot % 5 - 2) * 12.0, 0.0,
                        80.0 - (slot // 5) * 12.0, math.pi)
                       for slot in range(15)),
        },
        'routes': {
            '1': ({'id': 'test', 'capacity': 15, 'risk': 0.0,
                   'role_weights': {},
                   'waypoints': ((0.0, -40.0, False),
                                 (0.0, 40.0, False))},),
            '2': ({'id': 'test', 'capacity': 15, 'risk': 0.0,
                   'role_weights': {},
                   'waypoints': ((0.0, 40.0, False),
                                 (0.0, -40.0, False))},),
        },
    }

    def navigation_graph_loader(map_name):
        graph = dict(graph_template)
        graph['map'] = str(map_name)
        return graph

    def setup_turret_rotations(appearance):
        appearance.compoundModel.node('turret', appearance.turretMatrix)
        appearance.compoundModel.node(
            'gun_inclination', appearance.gunMatrix)

    def call_with_standard_gameplay_mask(callback, args=(), kwargs=None):
        if kwargs is None:
            kwargs = {}
        previous = bigworld.mapping_visibility_mask
        bigworld.mapping_visibility_mask = 0x00000001
        try:
            return callback(*args, **kwargs)
        finally:
            bigworld.mapping_visibility_mask = previous

    return types.SimpleNamespace(
        account_commands=types.SimpleNamespace(
            CMD_GET_AVATAR_SYNC=1, CMD_ADD_INT_USER_SETTINGS=2,
            CMD_DEL_INT_USER_SETTINGS=3),
        arena_cache={1: arena},
        arena_visibility_mask=lambda gameplay_id: 1 << int(gameplay_id),
        bigworld=bigworld,
        avatar_input_handler=types.SimpleNamespace(
            _CTRL_MODE=types.SimpleNamespace(
                ARCADE='arcade', SNIPER='sniper',
                STRATEGIC='strategic', POSTMORTEM='postmortem')),
        aih_constants=types.SimpleNamespace(
            GUN_MARKER_FLAG=types.SimpleNamespace(
                UNDEFINED=0, CONTROL_ENABLED=1, CLIENT_MODE_ENABLED=2,
                SERVER_MODE_ENABLED=4)),
        gun_marker_ctrl=types.SimpleNamespace(
            useClientGunMarker=lambda: True,
            useServerGunMarker=lambda: False),
        app_loader=app_loader,
        client_visibility_flags=types.SimpleNamespace(
            CLIENT_MASK=0xfff00000, SERVER_MASK=0x000fffff),
        compatibility=compatibility, constants=constants,
        battle_feedback_common=types.SimpleNamespace(
            BATTLE_EVENT_TYPE=types.SimpleNamespace(
                SPOTTED=0, RADIO_ASSIST=1, TRACK_ASSIST=2, CRIT=6,
                TANKING=5, DAMAGE=7, KILL=8, RECEIVED_CRIT=9,
                RECEIVED_DAMAGE=10, STUN_ASSIST=11, TARGET_VISIBILITY=12,
                packDamage=lambda damage, reason: (
                    (int(damage) << 16) | (int(reason) << 9)),
                packCrits=lambda count, reason: (
                    (int(count) << 16) | (int(reason) << 8)),
                packVisibility=lambda visible, direct: (
                    int(bool(visible)) | (int(bool(direct)) << 1)))),
        encode_gun_angles=lambda *unused: 0,
        game=types.SimpleNamespace(abort=mock.Mock()),
        gui_global_space_id=types.SimpleNamespace(
            LOBBY=4, BATTLE_LOADING=5, BATTLE=6),
        hangar_space=types.SimpleNamespace(
            g_hangarSpace=hangar_space),
        math=types.SimpleNamespace(
            Vector3=_Vector, Matrix=_Matrix,
            MatrixAnimation=_MatrixAnimation,
            MatrixInverse=_MatrixInverse, MatrixProduct=_MatrixProduct),
        model_assembler=types.SimpleNamespace(
            prepareCompoundAssembler=lambda descriptor, state, space, flag:
            descriptor,
            setupTurretRotations=setup_turret_rotations,
            setupVehicleFashion=lambda fashion, descriptor, crashed: True,
            createVehicleFilter=lambda descriptor: _VehicleFilter()),
        offline_map_creator=_OfflineMap(bigworld, app_loader),
        call_with_standard_gameplay_mask=call_with_standard_gameplay_mask,
        res_mgr=res_mgr,
        navigation_graph_loader=navigation_graph_loader,
        vehicle_view_state=types.SimpleNamespace(RPM='rpm', STUN='stun'),
        feedback_event_id=types.SimpleNamespace(VEHICLE_DEAD=17),
        vehicles=types.SimpleNamespace(
            VehicleDescr=_VehicleDescr,
            g_cache=types.SimpleNamespace(shotEffects={
                3: {
                    'armorRicochet': ('ricochetStages', 'ricochetFx', None),
                    'armorResisted': ('resistedStages', 'resistedFx', None),
                    'armorHit': ('hitStages', 'hitFx', None),
                    'armorSplashHit': ('splashStages', 'splashFx', None),
                    'targetStickers': {
                        'armorResisted': 7, 'armorPierced': 9},
                }})))


class NativeRemoteVehicleFactoryTests(unittest.TestCase):
    def _failing_registration_factory(self, arena_side_effect):
        runtime = _runtime()
        entities = {}
        bigworld = types.SimpleNamespace(
            entity=lambda entity_id: entities.get(int(entity_id)),
            entities=entities)
        binding = mock.Mock()
        binding.create_vehicle.return_value = 77
        binding.arena_vehicle_added.side_effect = arena_side_effect(entities)
        binding.destroy_entity.side_effect = (
            lambda entity_id: entities.pop(int(entity_id), None))
        factory = NativeRemoteVehicleFactory(
            bigworld, runtime.math, runtime.model_assembler, 7,
            binding=binding, compatibility=runtime.compatibility)
        return factory, binding, entities

    def test_failed_arena_registration_rolls_back_a_visible_native_entity(self):
        def failure(entities):
            def add_then_fail(*unused_args, **unused_kwargs):
                entities[77] = object()
                raise RuntimeError('arena registration failed')
            return add_then_fail

        factory, binding, entities = self._failing_registration_factory(
            failure)

        with self.assertRaisesRegex(RuntimeError, 'arena registration failed'):
            factory.create(
                object(), {}, _Vector(), (0.0, 0.0, 0.0))

        self.assertEqual({}, factory._states)
        self.assertEqual({}, factory._vehicles)
        self.assertEqual(set(), factory._failed_creates)
        self.assertEqual({}, entities)
        binding.destroy_entity.assert_called_once_with(77)
        factory.destroy_all()

    def test_failed_pending_create_keeps_a_tombstone_until_it_is_visible(self):
        def failure(unused_entities):
            return mock.Mock(side_effect=RuntimeError(
                'arena registration failed'))

        factory, binding, entities = self._failing_registration_factory(
            failure)

        with self.assertRaisesRegex(RuntimeError, 'arena registration failed'):
            factory.create(
                object(), {}, _Vector(), (0.0, 0.0, 0.0))

        binding.destroy_entity.assert_not_called()
        self.assertEqual({77}, factory._failed_creates)
        self.assertEqual({}, factory._states)
        self.assertEqual({}, factory._vehicles)
        entities[77] = object()

        self.assertTrue(factory._retire_failed_creates())

        binding.destroy_entity.assert_called_once_with(77)
        self.assertEqual(set(), factory._failed_creates)
        self.assertEqual({}, entities)
        factory.destroy_all()

    def test_failed_create_reports_original_and_cleanup_errors(self):
        def failure(entities):
            def add_then_fail(*unused_args, **unused_kwargs):
                entities[77] = object()
                raise RuntimeError('arena registration failed')
            return add_then_fail

        factory, binding, entities = self._failing_registration_factory(
            failure)
        binding.destroy_entity.side_effect = RuntimeError(
            'native cleanup failed')

        with self.assertRaisesRegex(
                RuntimeError,
                'arena registration failed.*native cleanup failed'):
            factory.create(
                object(), {}, _Vector(), (0.0, 0.0, 0.0))

        self.assertEqual({77}, factory._failed_creates)
        self.assertIn(77, entities)
        binding.destroy_entity.side_effect = (
            lambda entity_id: entities.pop(int(entity_id), None))
        factory.destroy_all()

    def test_native_destroy_retains_registries_until_engine_cleanup_succeeds(self):
        factory, binding, entities = self._failing_registration_factory(
            lambda unused_entities: None)
        entity_id = factory.create(
            object(), {}, _Vector(), (0.0, 0.0, 0.0))
        entities[entity_id] = object()
        binding.destroy_entity.side_effect = RuntimeError(
            'native destroy failed')

        with self.assertRaisesRegex(RuntimeError, 'native destroy failed'):
            factory.destroy(entity_id)

        self.assertIn(entity_id, factory._states)
        self.assertIn(entity_id, factory._vehicles)
        self.assertIn(entity_id, entities)

        binding.destroy_entity.side_effect = (
            lambda pending_id: entities.pop(int(pending_id), None))
        self.assertTrue(factory.destroy(entity_id))
        self.assertNotIn(entity_id, factory._states)
        self.assertNotIn(entity_id, factory._vehicles)
        self.assertNotIn(entity_id, entities)
        factory.destroy_all()

    def test_native_state_detach_retains_owner_until_overlay_cleanup_succeeds(self):
        runtime = _runtime()
        entity = types.SimpleNamespace(
            id=44, inWorld=True, isStarted=True,
            appearance=types.SimpleNamespace(onModelChanged=None))
        bigworld = types.SimpleNamespace(entities={44: entity})
        compatibility = mock.Mock()
        compatibility.clear_vehicle_pose_overlay.side_effect = RuntimeError(
            'overlay cleanup failed')
        state = _NativeRemoteState(
            bigworld, runtime.math, compatibility, None,
            _Vector(), (0.0, 0.0, 0.0))
        callback = object()
        state.entity = entity
        state.model_changed = callback

        with self.assertRaisesRegex(RuntimeError, 'overlay cleanup failed'):
            state.detach()

        self.assertIs(entity, state.entity)
        self.assertIs(callback, state.model_changed)

        compatibility.clear_vehicle_pose_overlay.side_effect = None
        self.assertTrue(state.detach())
        self.assertIsNone(state.entity)
        self.assertIsNone(state.model_changed)

    def test_native_siege_authority_uses_hydraulic_body_and_ground_chassis(self):
        runtime = _runtime()
        native_body = _Matrix()
        native_ground = _Matrix()
        set_delta = mock.Mock()
        descriptor = _Descriptor('sweden:S11_Strv_103B')
        descriptor.hasSiegeMode = True
        descriptor.gun.pitchLimits = {'absolute': (-0.2, 0.4)}
        entity = types.SimpleNamespace(
            typeDescriptor=descriptor, siegeState=2,
            filter=types.SimpleNamespace(
                bodyMatrix=native_body,
                groundPlacingMatrix=native_ground,
                getVehiclePhysics=lambda: types.SimpleNamespace(
                    setHullAimingAnglesDelta=set_delta)))
        state = _NativeRemoteState(
            types.SimpleNamespace(), runtime.math, mock.Mock(), None,
            _Vector(), (0.0, 0.05, 0.0), interpolate_motion=False,
            authority_geometry=True)
        state.entity = entity

        self.assertTrue(state.set_aim(0.0, 0.0, -0.45))
        body_matrix, chassis_matrix = state.collision_matrices()

        self.assertIs(state.matrix, chassis_matrix)
        self.assertIs(state._siege_relative_body_matrix, body_matrix.a)
        self.assertIs(state.matrix, body_matrix.b)
        self.assertIs(native_body, body_matrix.a.a)
        self.assertIs(native_ground, body_matrix.a.b.source)
        yaw_delta, pitch_delta = set_delta.call_args[0]
        self.assertEqual(0.0, yaw_delta)
        self.assertAlmostEqual(-0.3, pitch_delta)
        self.assertAlmostEqual(-0.2, state.aim.gunMatrix.pitch)

        # SWITCHING_OFF retains the enabled hydraulic pose until the
        # authoritative DISABLED edge completes the transition.
        entity.siegeState = 3
        switching_body, switching_chassis = state.collision_matrices()
        self.assertIs(body_matrix, switching_body)
        self.assertIs(state.matrix, switching_chassis)
        entity.siegeState = 0
        disabled_body, disabled_chassis = state.collision_matrices()
        self.assertIs(state.matrix, disabled_body)
        self.assertIs(state.matrix, disabled_chassis)

    def test_visible_native_siege_vehicle_keeps_existing_presentation_path(self):
        runtime = _runtime()
        descriptor = _Descriptor('sweden:S11_Strv_103B')
        descriptor.hasSiegeMode = True
        state = _NativeRemoteState(
            types.SimpleNamespace(), runtime.math, mock.Mock(), None,
            _Vector(), (0.0, 0.0, 0.0), interpolate_motion=False)
        state.entity = types.SimpleNamespace(
            typeDescriptor=descriptor, siegeState=2)

        self.assertTrue(state.set_aim(0.0, 0.0, -0.45))
        body_matrix, chassis_matrix = state.collision_matrices()

        self.assertAlmostEqual(-0.45, state.aim.gunMatrix.pitch)
        self.assertIs(state.matrix, body_matrix)
        self.assertIs(state.matrix, chassis_matrix)

    def test_native_vehicle_owns_stock_appearance_and_lan_pose_overlay(self):
        runtime = _runtime()
        holder = {}
        motion_lod_link = object()
        data_links = types.SimpleNamespace(
            createFloatLink=mock.Mock(
                side_effect=AssertionError(
                    'plain Python owners must not use DataLinks')),
            linkMatrixTranslation=mock.Mock(
                return_value=motion_lod_link))
        binding = BigWorldVehicleBinding(
            runtime.bigworld, runtime.bigworld.avatar, runtime.constants,
            _VehicleDescr, runtime.encode_gun_angles,
            outfit_provider=lambda unused_descriptor: '',
            authority_entity_resolver=lambda entity_id:
            holder['factory'].get(entity_id))
        factory = NativeRemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7,
            binding=binding, compatibility=runtime.compatibility,
            data_links=data_links)
        holder['factory'] = factory
        properties = binding.properties_from_compact_descr(
            'ussr:R11_MS-1', 2, 'Native remote')

        vehicle_id = factory.create(
            _Descriptor(), properties, _Vector(1.0, 2.0, 3.0),
            (0.0, 0.0, 0.25))

        self.assertIsNone(runtime.bigworld.entity(vehicle_id))
        self.assertEqual(runtime.constants.ARENA_UPDATE.VEHICLE_ADDED,
                         runtime.bigworld.avatar.arena_updates[-1][0])
        pending = runtime.bigworld.pending_entities[vehicle_id]
        pending._offlineNativeMarkerVisible = False
        pending._offlineNativeDrawVisible = False
        pending.filter.movementInfo = object()
        pending.filter.setTracksSpeed = mock.Mock()
        pending.appearance.filter = pending.filter
        pending.appearance.flyingInfoProvider = types.SimpleNamespace(
            isLeftSideFlying=True, isRightSideFlying=False)
        detailed_engine = types.SimpleNamespace(
            vehicleSpeedLink=None, rotationSpeedLink=None)
        pending.appearance.detailedEngineState = detailed_engine
        pending.appearance.engineAudition = object()
        wheels_animator = types.SimpleNamespace(
            setMovementInfo=mock.Mock())
        pending.appearance.wheelsAnimator = wheels_animator
        pending.appearance.suspension = object()
        motion_lod = types.SimpleNamespace(setupPosition=mock.Mock())
        pending.appearance.lodCalculator = motion_lod
        native_placing_compensation = object()
        swinging = types.SimpleNamespace(
            worldMatrix=object(),
            placingCompensationMatrix=native_placing_compensation)
        pending.appearance.swingingAnimator = swinging
        runtime.bigworld.enter_pending_vehicle(vehicle_id)
        self.assertTrue(factory.is_ready(vehicle_id))
        vehicle = factory.get(vehicle_id)
        self.assertTrue(vehicle._offlineNativeRemote)
        self.assertFalse(vehicle._offlineNativeMarkerVisible)
        self.assertFalse(vehicle._offlineNativeDrawVisible)
        self.assertIs(vehicle.model.matrix, vehicle.matrix)
        self.assertEqual(1, len(vehicle.aim_targets))
        self.assertIsNotNone(vehicle.track_scroll)
        self.assertIs(swinging.worldMatrix, vehicle.model.matrix)
        state = factory._states[vehicle_id]
        self.assertIs(
            state._identity_placing_compensation,
            swinging.placingCompensationMatrix)
        self.assertIsNot(
            native_placing_compensation,
            swinging.placingCompensationMatrix)
        self.assertTrue(callable(detailed_engine.vehicleSpeedLink))
        self.assertTrue(callable(detailed_engine.rotationSpeedLink))
        self.assertEqual(0.0, detailed_engine.vehicleSpeedLink())
        self.assertEqual(0.0, detailed_engine.rotationSpeedLink())
        data_links.createFloatLink.assert_not_called()
        data_links.linkMatrixTranslation.assert_called_once_with(
            vehicle.model.matrix)
        motion_lod.setupPosition.assert_called_once_with(motion_lod_link)
        # Stock createWheelsAnimator already bound filter.movementInfo. The
        # LAN pose-provider swap changes only the shared LOD position link.
        wheels_animator.setMovementInfo.assert_not_called()
        self.assertEqual(
            {'engine_audio_motion': True, 'body_swinging': True,
             'stock_motion_lod': True, 'stock_wheels': True,
             'stock_suspension': True},
            dict(vehicle._offlinePresentationCapabilities))

        binding.set_vehicle_pose(
            vehicle_id, _Vector(8.0, 2.5, 9.0), (0.0, 0.0, 0.75),
            relax_time=0.1, now=10.0)
        binding.set_vehicle_pose(
            vehicle_id, _Vector(8.0, 2.5, 11.0), (0.0, 0.0, 1.0),
            relax_time=0.1, now=11.0)
        binding.update_vehicle_aim(vehicle_id, 0.75, 1.0, -0.1)
        self.assertEqual((8.0, 2.5, 11.0), tuple(vehicle.position))
        overlay = runtime.compatibility.pose_overlays[id(vehicle)]
        self.assertEqual((0.0, 0.0, 2.0), tuple(overlay['velocity']))
        self.assertEqual((0.0, 0.0, 2.0), tuple(overlay['acceleration']))
        self.assertAlmostEqual(2.0 * math.cos(1.0), overlay['speed'])
        self.assertAlmostEqual(0.25, overlay['turn_speed'])
        self.assertAlmostEqual(
            overlay['speed'], detailed_engine.vehicleSpeedLink())
        self.assertAlmostEqual(
            overlay['turn_speed'], detailed_engine.rotationSpeedLink())
        self.assertAlmostEqual(1.0, vehicle._aim_yaw)
        self.assertAlmostEqual(-0.1, vehicle._gun_pitch)
        self.assertTrue(vehicle.settle_motion(now=12.0))
        overlay = runtime.compatibility.pose_overlays[id(vehicle)]
        self.assertEqual((0.0, 0.0, 0.0), tuple(overlay['velocity']))
        self.assertEqual((0.0, 0.0, 0.0), tuple(overlay['acceleration']))
        self.assertEqual(0.0, overlay['speed'])
        self.assertEqual(0.0, overlay['turn_speed'])
        self.assertEqual(0.0, detailed_engine.vehicleSpeedLink())
        self.assertEqual(0.0, detailed_engine.rotationSpeedLink())
        self.assertTrue(vehicle.update_tracks(
            2.0, 3.0, (ENGINE_MODE_RUNNING, 1)))
        self.assertTrue(vehicle.update_tracks(
            2.0, 3.0, (ENGINE_MODE_RUNNING, 1)))
        vehicle.filter.setTracksSpeed.assert_called_once_with(
            2.0, False, 3.0, True)
        self.assertEqual([(2.0, 3.0)], vehicle.track_scrolls)

        pending.appearance.flyingInfoProvider.isLeftSideFlying = False
        self.assertTrue(vehicle.update_tracks(
            2.0, 3.0, (ENGINE_MODE_RUNNING, 1)))
        self.assertEqual([
            mock.call(2.0, False, 3.0, True),
            mock.call(2.0, True, 3.0, True),
        ], vehicle.filter.setTracksSpeed.call_args_list)
        self.assertEqual([(2.0, 3.0), (2.0, 3.0)],
                         vehicle.track_scrolls)

        # A controller object alone is not proof that its native filter is
        # bound.  Do not cache a failed feed: the identical feed must reach
        # setTracksSpeed once the stock appearance/filter link is restored.
        pending.appearance.filter = object()
        self.assertFalse(vehicle.update_tracks(
            4.0, 5.0, (ENGINE_MODE_RUNNING, 1)))
        self.assertEqual(2, vehicle.filter.setTracksSpeed.call_count)
        pending.appearance.filter = pending.filter
        self.assertTrue(vehicle.update_tracks(
            4.0, 5.0, (ENGINE_MODE_RUNNING, 1)))
        self.assertEqual(3, vehicle.filter.setTracksSpeed.call_count)
        self.assertEqual(mock.call(4.0, True, 5.0, True),
                         vehicle.filter.setTracksSpeed.call_args)

        # Never call the fatal native WGVehicleFilter method after the engine
        # table stops owning this exact PyEntity, including fallback methods
        # on the now-invalid appearance.
        runtime.bigworld.entities.pop(vehicle_id)
        self.assertFalse(vehicle.update_tracks(
            4.0, 5.0, (ENGINE_MODE_RUNNING, 1)))
        self.assertEqual(3, vehicle.filter.setTracksSpeed.call_count)
        self.assertEqual([(2.0, 3.0), (2.0, 3.0),
                          (4.0, 5.0), (4.0, 5.0)],
                         vehicle.track_scrolls)
        self.assertFalse(
            vehicle._offlinePresentationCapabilities[
                'engine_owned_track_motion'])
        runtime.bigworld.entities[vehicle_id] = vehicle

        class _RejectingAnimation(object):
            def __setattr__(self, name, value):
                if name in ('keyframes', 'time'):
                    raise RuntimeError('animation rejected rekey')
                object.__setattr__(self, name, value)

        rejected = _RejectingAnimation()
        state.animation = rejected
        state.provider = rejected
        vehicle.model.matrix = rejected
        swinging.worldMatrix = rejected
        swinging.placingCompensationMatrix = native_placing_compensation
        self.assertFalse(state._rekey(0.1))
        self.assertIs(state.matrix, vehicle.model.matrix)
        self.assertIs(state.matrix, swinging.worldMatrix)
        self.assertIs(
            state._identity_placing_compensation,
            swinging.placingCompensationMatrix)
        self.assertEqual(2, data_links.linkMatrixTranslation.call_count)
        data_links.linkMatrixTranslation.assert_called_with(state.matrix)
        self.assertEqual(2, motion_lod.setupPosition.call_count)
        motion_lod.setupPosition.assert_called_with(motion_lod_link)
        wheels_animator.setMovementInfo.assert_not_called()

        self.assertTrue(factory.destroy(vehicle_id))
        self.assertIsNone(runtime.bigworld.entity(vehicle_id))
        self.assertNotIn(id(vehicle), runtime.compatibility.pose_overlays)

    def test_native_motion_capabilities_fall_back_without_stock_animators(self):
        runtime = _runtime()
        holder = {}
        binding = BigWorldVehicleBinding(
            runtime.bigworld, runtime.bigworld.avatar, runtime.constants,
            _VehicleDescr, runtime.encode_gun_angles,
            outfit_provider=lambda unused_descriptor: '',
            authority_entity_resolver=lambda entity_id:
            holder['factory'].get(entity_id))
        factory = NativeRemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7,
            binding=binding, compatibility=runtime.compatibility)
        holder['factory'] = factory
        properties = binding.properties_from_compact_descr(
            'ussr:R11_MS-1', 2, 'Native fallback')

        vehicle_id = factory.create(
            _Descriptor(), properties, _Vector(), (0.0, 0.0, 0.0))
        runtime.bigworld.enter_pending_vehicle(vehicle_id)
        self.assertTrue(factory.is_ready(vehicle_id))
        vehicle = factory.get(vehicle_id)

        self.assertEqual(
            {'engine_audio_motion': False, 'body_swinging': False,
             'stock_motion_lod': False, 'stock_wheels': False,
             'stock_suspension': False},
            dict(vehicle._offlinePresentationCapabilities))
        # Without the engine-owned filter write the feed stays uncached and
        # returns False, so the same values may retry; PyTrackScroll still
        # receives every fallback feed.
        self.assertFalse(vehicle.update_tracks(
            1.0, 1.5, (ENGINE_MODE_RUNNING, 1)))
        self.assertEqual([(1.0, 1.5)], vehicle.track_scrolls)
        self.assertFalse(
            vehicle._offlinePresentationCapabilities[
                'engine_owned_track_motion'])
        self.assertFalse(vehicle.update_tracks(
            1.0, 1.5, (ENGINE_MODE_RUNNING, 1)))
        self.assertEqual([(1.0, 1.5), (1.0, 1.5)], vehicle.track_scrolls)
        self.assertTrue(factory.destroy(vehicle_id))

    def test_guest_native_vehicle_uses_one_stable_pose_matrix(self):
        runtime = _runtime()
        holder = {}
        binding = BigWorldVehicleBinding(
            runtime.bigworld, runtime.bigworld.avatar, runtime.constants,
            _VehicleDescr, runtime.encode_gun_angles,
            outfit_provider=lambda unused_descriptor: '',
            authority_entity_resolver=lambda entity_id:
            holder['factory'].get(entity_id))
        factory = NativeRemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7,
            binding=binding, compatibility=runtime.compatibility,
            interpolate_motion=False)
        holder['factory'] = factory
        properties = binding.properties_from_compact_descr(
            'ussr:R11_MS-1', 2, 'Native guest')

        vehicle_id = factory.create(
            _Descriptor(), properties, _Vector(), (0.0, 0.0, 0.0))
        runtime.bigworld.enter_pending_vehicle(vehicle_id)
        self.assertTrue(factory.is_ready(vehicle_id))
        vehicle = factory.get(vehicle_id)
        state = factory._states[vehicle_id]
        provider = vehicle.model.matrix

        self.assertIsNone(state.animation)
        self.assertIs(state.matrix, provider)
        binding.set_vehicle_pose(
            vehicle_id, _Vector(1.0, 0.0, 2.0), (0.0, 0.0, 0.2),
            now=10.0)
        binding.set_vehicle_pose(
            vehicle_id, _Vector(2.0, 0.0, 4.0), (0.0, 0.0, 0.4),
            now=10.1)
        self.assertIs(provider, vehicle.model.matrix)

        vehicle._offlineNativeDrawVisible = False
        vehicle._spot_visible = False
        vehicle.model = _Model()
        vehicle.appearance.compoundModel = vehicle.model
        for handler in tuple(vehicle.appearance.onModelChanged.handlers):
            handler()
        self.assertIs(provider, vehicle.model.matrix)
        self.assertFalse(vehicle.model.visible)
        self.assertEqual([], vehicle.targetCaps)

        # A late stock wreck-model relink must preserve world visibility while
        # never turning the dead vehicle back into a spotting-gated target.
        vehicle.health = 0
        vehicle.isCrewActive = False
        vehicle._offlineNativeDrawVisible = True
        vehicle._spot_visible = True
        vehicle.model = _Model()
        vehicle.appearance.compoundModel = vehicle.model
        for handler in tuple(vehicle.appearance.onModelChanged.handlers):
            handler()
        self.assertIs(provider, vehicle.model.matrix)
        self.assertTrue(vehicle.model.visible)
        self.assertEqual([], vehicle.targetCaps)

        self.assertTrue(factory.set_entity_interpolate_motion(
            vehicle_id, True))
        self.assertIsNotNone(state.animation)
        self.assertIs(state.animation, vehicle.model.matrix)
        self.assertTrue(factory.set_entity_interpolate_motion(
            vehicle_id, False))
        self.assertIsNone(state.animation)
        self.assertIs(state.matrix, vehicle.model.matrix)
        self.assertTrue(factory.destroy(vehicle_id))

    def test_minimap_refresh_replays_only_the_native_added_signal(self):
        runtime = _runtime()
        entity = _Vehicle(
            77, _Descriptor(), _Vector(), (0.0, 0.0, 0.0),
            {'health': 500, 'publicInfo': {'team': 1}})
        entity.proxy = types.SimpleNamespace(id=77)
        runtime.bigworld.entities[77] = entity
        arena_dp = runtime.bigworld.avatar.guiSessionProvider.getArenaDP()
        gui_props = object()
        arena_dp.getPlayerGuiProps = mock.Mock(return_value=gui_props)
        feedback = runtime.bigworld.avatar.guiSessionProvider.shared.feedback
        feedback.onMinimapVehicleAdded = mock.Mock()
        binding = BigWorldVehicleBinding(
            runtime.bigworld, runtime.bigworld.avatar, runtime.constants,
            _VehicleDescr, runtime.encode_gun_angles,
            outfit_provider=lambda unused_descriptor: '')

        self.assertTrue(binding.refresh_vehicle_minimap(77))

        vehicle_info = arena_dp.getVehicleInfo(77)
        arena_dp.getPlayerGuiProps.assert_called_once_with(77, 1)
        feedback.onMinimapVehicleAdded.assert_called_once_with(
            entity.proxy, vehicle_info, gui_props)
        self.assertEqual({77}, feedback._BattleFeedbackAdaptor__visible)

    def test_binding_splits_world_marker_from_minimap_signals(self):
        runtime = _runtime()
        entity = _Vehicle(
            77, _Descriptor(), _Vector(), (0.0, 0.0, 0.0),
            {'health': 500, 'publicInfo': {'team': 2}})
        entity.proxy = types.SimpleNamespace(id=77)
        runtime.bigworld.entities[77] = entity
        arena_dp = runtime.bigworld.avatar.guiSessionProvider.getArenaDP()
        gui_props = object()
        arena_dp.getPlayerGuiProps = mock.Mock(return_value=gui_props)
        feedback = runtime.bigworld.avatar.guiSessionProvider.shared.feedback
        feedback.onVehicleMarkerAdded = mock.Mock()
        feedback.onVehicleMarkerRemoved = mock.Mock()
        feedback.onMinimapVehicleAdded = mock.Mock()
        feedback.onMinimapVehicleRemoved = mock.Mock()
        binding = BigWorldVehicleBinding(
            runtime.bigworld, runtime.bigworld.avatar, runtime.constants,
            _VehicleDescr, runtime.encode_gun_angles,
            outfit_provider=lambda unused_descriptor: '')

        self.assertTrue(binding.start_vehicle_marker(77))
        self.assertTrue(binding.stop_vehicle_marker(77))
        self.assertTrue(binding.start_vehicle_minimap(77))
        self.assertTrue(binding.stop_vehicle_minimap(77))

        vehicle_info = arena_dp.getVehicleInfo(77)
        feedback.onVehicleMarkerAdded.assert_called_once_with(
            entity.proxy, vehicle_info, gui_props)
        feedback.onVehicleMarkerRemoved.assert_called_once_with(77)
        feedback.onMinimapVehicleAdded.assert_called_once_with(
            entity.proxy, vehicle_info, gui_props)
        feedback.onMinimapVehicleRemoved.assert_called_once_with(77)
        self.assertEqual(set(), feedback._BattleFeedbackAdaptor__visible)


class RemoteVehicleFactoryTests(unittest.TestCase):
    def _ready_native_factory(self):
        runtime = _runtime()
        holder = {}
        binding = BigWorldVehicleBinding(
            runtime.bigworld, runtime.bigworld.avatar, runtime.constants,
            _VehicleDescr, runtime.encode_gun_angles,
            outfit_provider=lambda unused_descriptor: '',
            authority_entity_resolver=lambda entity_id:
            holder['factory'].get(entity_id))
        factory = NativeRemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7,
            binding=binding, compatibility=runtime.compatibility)
        holder['factory'] = factory
        properties = binding.properties_from_compact_descr(
            'ussr:R11_MS-1', 2, 'Native remote')
        vehicle_id = factory.create(
            _Descriptor(), properties, _Vector(0.0, 0.0, 20.0),
            (0.0, 0.0, 0.0))
        runtime.bigworld.enter_pending_vehicle(vehicle_id)
        self.assertTrue(factory.is_ready(vehicle_id))
        return runtime, factory, binding, vehicle_id, factory.get(vehicle_id)

    def test_sticker_owner_survives_failed_native_detach(self):
        runtime = _runtime()
        vehicle = RemoteVehicle(
            11, _Descriptor(), {}, _Vector(), (0.0, 0.0, 0.0),
            runtime.math)
        stickers = types.SimpleNamespace(
            detach=mock.Mock(side_effect=(
                RuntimeError('sticker detach failed'), None)))
        vehicle.attach_stickers(stickers)

        with self.assertRaisesRegex(RuntimeError, 'sticker detach failed'):
            vehicle._release_stickers()

        self.assertIs(stickers, vehicle._vehicle_stickers)
        self.assertTrue(vehicle._release_stickers())
        self.assertIsNone(vehicle._vehicle_stickers)
        self.assertEqual(2, stickers.detach.call_count)

    def test_remote_appearance_delegates_damage_sticker_to_stock_owner(self):
        runtime = _runtime()
        vehicle = RemoteVehicle(
            11, _Descriptor(), {}, _Vector(), (0.0, 0.0, 0.0),
            runtime.math)
        stickers = types.SimpleNamespace(addDamageSticker=mock.Mock())
        vehicle.attach_stickers(stickers)
        start = _Vector(0.0, 0.0, -1.0)
        end = _Vector(0.0, 0.0, 1.0)

        vehicle.appearance.addDamageSticker(
            123, 'hull', 7, start, end)

        stickers.addDamageSticker.assert_called_once_with(
            123, 'hull', 7, start, end)

    def test_damage_sticker_encoder_clips_and_packs_component_local_ray(self):
        descriptor = _Descriptor()
        descriptor.chassis.hullPosition = _Vector()
        descriptor.hull.hitTester = types.SimpleNamespace(bbox=(
            _Vector(-1.0, -1.0, -1.0),
            _Vector(1.0, 1.0, 1.0), None))
        vehicle = _Vehicle(
            11, descriptor, _Vector(), (0.0, 0.0, 0.0),
            {'health': 500})
        math_module = types.SimpleNamespace(
            Vector3=_Vector, Matrix=_Matrix)

        encoded = remote_vehicle_module.encode_damage_sticker(
            vehicle, vehicle.matrix,
            _Vector(0.0, 0.0, -3.0), _Vector(0.0, 0.0, 3.0),
            'vehicleHull', 37, math_module)

        self.assertIsNotNone(encoded)
        self.assertEqual(37, encoded & 255)
        self.assertEqual(1, (encoded >> 8) & 255)
        self.assertEqual(
            [128, 128, 0, 128, 128, 255],
            [(encoded >> shift) & 255
             for shift in (16, 24, 32, 40, 48, 56)])
        self.assertIsNone(remote_vehicle_module.encode_damage_sticker(
            vehicle, vehicle.matrix,
            _Vector(2.0, 0.0, -3.0), _Vector(2.0, 0.0, 3.0),
            'vehicleHull', 37, math_module))

    def test_failed_effect_detach_keeps_entity_and_effect_for_retry(self):
        runtime = _runtime()
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        vehicle_id = factory.create(_Descriptor(), {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True,
            'gunAnglesPacked': 0}, _Vector(), (0.0, 0.0, 0.0))
        vehicle = factory.get(vehicle_id)
        visual_id = vehicle.bw_entity_id
        effects = types.SimpleNamespace(
            destroy=mock.Mock(side_effect=(
                RuntimeError('effect detach failed'), None)))
        vehicle.appearance._bound_effects = effects
        destroy_entity = mock.Mock(wraps=runtime.bigworld.destroyEntity)
        runtime.bigworld.destroyEntity = destroy_entity

        with self.assertRaisesRegex(RuntimeError, 'effect detach failed'):
            factory.destroy(vehicle_id)

        self.assertIs(vehicle, factory.get(vehicle_id))
        self.assertIs(effects, vehicle.appearance._bound_effects)
        self.assertIsNotNone(vehicle.bw_entity)
        destroy_entity.assert_not_called()
        self.assertTrue(factory.destroy(vehicle_id))
        self.assertIsNone(factory.get(vehicle_id))
        self.assertEqual(2, effects.destroy.call_count)
        destroy_entity.assert_called_once_with(visual_id)
        factory.destroy_all()

    def test_retained_wreck_keeps_failed_effect_owner_for_final_cleanup(self):
        runtime = _runtime()
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        vehicle_id = factory.create(_Descriptor(), {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True,
            'gunAnglesPacked': 0}, _Vector(), (0.0, 0.0, 0.0))
        vehicle = factory.get(vehicle_id)
        effects = types.SimpleNamespace(
            destroy=mock.Mock(side_effect=(
                RuntimeError('wreck effect detach failed'), None)))
        vehicle.appearance._bound_effects = effects

        self.assertTrue(vehicle.retain_wreck_model())
        self.assertIs(effects, vehicle.appearance._bound_effects)

        factory.destroy_all()

        self.assertIsNone(vehicle.appearance._bound_effects)
        self.assertEqual(2, effects.destroy.call_count)

    def test_partial_track_scroll_setup_unwinds_native_callback_and_data(self):
        runtime = _runtime()
        fashions = [types.SimpleNamespace(movementInfo=None)]
        camouflages = types.SimpleNamespace(
            prepareFashions=mock.Mock(return_value=fashions),
            updateFashions=mock.Mock())
        scroll = _TrackScroll()

        def reject_filter(value):
            scroll.data = value
            if value is not None:
                raise RuntimeError('native filter was rejected')

        scroll.setData = mock.Mock(side_effect=reject_filter)
        runtime.bigworld.PyTrackScroll = mock.Mock(return_value=scroll)
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7,
            camouflages=camouflages, enable_track_animation=True)
        vehicle = types.SimpleNamespace(
            id=1000, publicInfo={}, _offline_outfit_valid=None)
        model = _Model()

        self.assertFalse(factory._assemble_track_animation(
            vehicle, _Descriptor(), model))

        self.assertFalse(scroll.active)
        self.assertIsNone(scroll.data)
        self.assertEqual(2, scroll.setData.call_count)
        self.assertIsNotNone(factory.track_animation_error)

    def test_remote_outfit_updates_model_fashions_without_track_animation(self):
        runtime = _runtime()
        fashions = [types.SimpleNamespace(movementInfo=None)]
        parsed_outfit = object()
        camouflages = types.SimpleNamespace(
            prepareFashions=mock.Mock(return_value=fashions),
            updateFashions=mock.Mock())
        outfit_factory = mock.Mock(return_value=parsed_outfit)
        stickers = types.SimpleNamespace(
            setClanID=mock.Mock(), attach=mock.Mock(), detach=mock.Mock())
        stickers_factory = mock.Mock(return_value=stickers)
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7,
            camouflages=camouflages, enable_track_animation=False,
            outfit_factory=outfit_factory,
            vehicle_stickers_factory=stickers_factory)
        vehicle_id = factory.create(_Descriptor(), {
            'publicInfo': {
                'team': 2, 'name': 'Remote', 'marksOnGun': 0,
                'clanDBID': 0, 'outfit': b'remote-outfit'},
            'health': 500, 'isCrewActive': True,
            'gunAnglesPacked': 0}, _Vector(), (0.0, 0.0, 0.0))

        vehicle = factory.get(vehicle_id)
        outfit_factory.assert_called_once_with(b'remote-outfit')
        camouflages.updateFashions.assert_called_once_with(
            fashions, vehicle.typeDescriptor, False, parsed_outfit)
        self.assertIs(fashions, vehicle.model.fashions)
        self.assertIsNone(vehicle.track_scroll)
        stickers_factory.assert_called_once_with(
            vehicle.typeDescriptor, 0, parsed_outfit)
        stickers.attach.assert_called_once_with(vehicle.model, False, False)
        factory.destroy(vehicle_id)
        stickers.detach.assert_called_once_with()

    def test_bad_remote_outfit_falls_back_to_empty_visuals(self):
        runtime = _runtime()
        fashions = [types.SimpleNamespace(movementInfo=None)]
        empty_outfit = object()

        def outfit_factory(compact=None):
            if compact:
                raise ValueError('bad outfit')
            return empty_outfit

        camouflages = types.SimpleNamespace(
            prepareFashions=mock.Mock(return_value=fashions),
            updateFashions=mock.Mock())
        stickers = types.SimpleNamespace(
            setClanID=mock.Mock(), attach=mock.Mock(), detach=mock.Mock())
        stickers_factory = mock.Mock(return_value=stickers)
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7,
            camouflages=camouflages, enable_track_animation=False,
            outfit_factory=outfit_factory,
            vehicle_stickers_factory=stickers_factory)
        vehicle_id = factory.create(_Descriptor(), {
            'publicInfo': {
                'team': 2, 'name': 'Remote', 'outfit': b'bad'},
            'health': 500, 'isCrewActive': True,
            'gunAnglesPacked': 0}, _Vector(), (0.0, 0.0, 0.0))

        vehicle = factory.get(vehicle_id)
        camouflages.updateFashions.assert_not_called()
        self.assertIs(fashions, vehicle.model.fashions)
        stickers_factory.assert_called_once_with(
            vehicle.typeDescriptor, 0, empty_outfit)

    def test_pose_collider_uses_visible_matrix_not_stale_native_pose(self):
        descriptor = _Descriptor()
        material = types.SimpleNamespace(armor=75.0)
        hit_tester = types.SimpleNamespace(
            localHitTest=mock.Mock(return_value=[
                (20.0, None, 1.0, 7)]))
        descriptor.hull.hitTester = hit_tester
        descriptor.hull.materials = {7: material}
        vehicle = _Vehicle(
            11, descriptor, _Vector(0.0, 0.0, 0.0),
            (0.0, 0.0, 0.0), {'health': 500})
        visible_matrix = _Matrix()
        visible_matrix.translation = _Vector(0.0, 0.0, 20.0)

        collisions = collide_vehicle_at_matrix(
            vehicle, visible_matrix, _Vector(0.0, 1.0, 0.0),
            _Vector(0.0, 1.0, 100.0),
            types.SimpleNamespace(Vector3=_Vector, Matrix=_Matrix))

        self.assertEqual(1, len(collisions))
        self.assertEqual(20.0, collisions[0].dist)
        self.assertIs(material, collisions[0].matInfo)
        self.assertEqual('vehicleHull', collisions[0].compName)
        self.assertEqual(4, len(collisions[0]))
        local_start, local_end = hit_tester.localHitTest.call_args[0]
        self.assertEqual(-20.0, local_start.z)
        self.assertEqual(80.0, local_end.z)

    def test_pose_collider_keeps_hydraulic_chassis_on_ground_matrix(self):
        descriptor = _Descriptor()
        chassis_tester = types.SimpleNamespace(
            localHitTest=mock.Mock(return_value=[]))
        hull_tester = types.SimpleNamespace(
            localHitTest=mock.Mock(return_value=[]))
        descriptor.chassis.hitTester = chassis_tester
        descriptor.hull.hitTester = hull_tester
        vehicle = _Vehicle(
            11, descriptor, _Vector(), (0.0, 0.0, 0.0),
            {'health': 500})
        body_matrix = _Matrix()
        body_matrix.translation = _Vector(0.0, 0.0, 20.0)
        chassis_matrix = _Matrix()
        chassis_matrix.translation = _Vector(0.0, 0.0, 10.0)

        collide_vehicle_at_matrix(
            vehicle, body_matrix, _Vector(0.0, 1.0, 0.0),
            _Vector(0.0, 1.0, 100.0),
            types.SimpleNamespace(Vector3=_Vector, Matrix=_Matrix),
            chassis_matrix=chassis_matrix)

        chassis_start = chassis_tester.localHitTest.call_args[0][0]
        hull_start = hull_tester.localHitTest.call_args[0][0]
        self.assertEqual(-10.0, chassis_start.z)
        self.assertEqual(-20.0, hull_start.z)

    def test_remote_collision_preserves_ext_shape_across_ticks_and_skip_gun(self):
        descriptor = _Descriptor()
        gun_material = types.SimpleNamespace(armor=25.0)
        hull_material = types.SimpleNamespace(armor=75.0)
        descriptor.gun.hitTester = types.SimpleNamespace(
            localHitTest=mock.Mock(return_value=[
                (4.0, None, 0.8, 3)]))
        descriptor.gun.materials = {3: gun_material}
        descriptor.hull.hitTester = types.SimpleNamespace(
            localHitTest=mock.Mock(return_value=[
                (12.0, None, 0.9, 7)]))
        descriptor.hull.materials = {7: hull_material}
        vehicle = RemoteVehicle(
            1000, descriptor, {
                'publicInfo': {'team': 2, 'name': 'Bot'},
                'health': 500, 'isCrewActive': True,
                'gunAnglesPacked': 0},
            _Vector(), (0.0, 0.0, 0.0),
            types.SimpleNamespace(Vector3=_Vector, Matrix=_Matrix))
        start = _Vector(0.0, 1.0, -20.0)
        end = _Vector(0.0, 1.0, 80.0)

        for unused_tick in range(5):
            collisions = vehicle.collideSegmentExt(start, end)
            self.assertEqual(
                ['vehicleGun', 'vehicleHull'],
                [collision.compName for collision in collisions])
            self.assertTrue(all(len(collision) == 4
                                for collision in collisions))
            self.assertEqual(
                [gun_material, hull_material],
                [collision.matInfo for collision in collisions])

        nearest = vehicle.collideSegment(start, end, skipGun=True)
        self.assertEqual(12.0, nearest.dist)
        self.assertEqual(0.9, nearest.hitAngleCos)
        self.assertEqual(75.0, nearest.armor)

    def test_pose_collider_rotates_ray_with_visible_hull_yaw(self):
        descriptor = _Descriptor()
        hit_tester = types.SimpleNamespace(
            localHitTest=mock.Mock(return_value=[
                (10.0, None, 1.0, 7)]))
        descriptor.hull.hitTester = hit_tester
        descriptor.hull.materials = {
            7: types.SimpleNamespace(armor=75.0)}
        vehicle = _Vehicle(
            11, descriptor, _Vector(), (0.0, 0.0, 0.0),
            {'health': 500})
        visible_matrix = _YawMatrix()
        visible_matrix.setRotateYPR((math.pi / 2.0, 0.0, 0.0))
        visible_matrix.translation = _Vector(10.0, 0.0, 20.0)

        collisions = collide_vehicle_at_matrix(
            vehicle, visible_matrix, _Vector(0.0, 1.0, 20.0),
            _Vector(20.0, 1.0, 20.0),
            types.SimpleNamespace(Vector3=_Vector, Matrix=_YawMatrix))

        self.assertEqual(1, len(collisions))
        local_start, local_end = hit_tester.localHitTest.call_args[0]
        self.assertAlmostEqual(-10.0, local_start.z)
        self.assertAlmostEqual(10.0, local_end.z)
        self.assertAlmostEqual(0.0, local_start.x)
        self.assertAlmostEqual(0.0, local_end.x)

    def test_remote_engine_audition_uses_exact_1513_sound_object(self):
        vehicle = RemoteVehicle(
            1000, _Descriptor(), {
                'publicInfo': {'team': 2, 'name': 'Bot'},
                'health': 500, 'isCrewActive': True,
                'gunAnglesPacked': 0},
            _Vector(), (0.0, 0.0, 0.0),
            types.SimpleNamespace(Vector3=_Vector, Matrix=_Matrix))
        vehicle.model = _Model()
        sound_object = mock.Mock()
        sound_group = types.SimpleNamespace(
            WWgetSoundObject=mock.Mock(return_value=sound_object))
        sound_module = types.ModuleType('SoundGroups')
        sound_module.g_instance = sound_group

        with mock.patch.dict(sys.modules, {'SoundGroups': sound_module}):
            first = vehicle.appearance.engineAudition.getSoundObject(3)
            second = vehicle.appearance.engineAudition.getSoundObject(3)

        self.assertIs(sound_object, first)
        self.assertIs(first, second)
        sound_group.WWgetSoundObject.assert_called_once()
        name, node = sound_group.WWgetSoundObject.call_args[0]
        self.assertEqual('offline_lan_vehicle_1000_sound_3', name)
        self.assertIsNotNone(node)

    def test_abandoning_a_visual_releases_its_cached_sound_objects(self):
        """A _WWISE.SoundObject belongs to the sound engine and the WorldApp
        scene, not to the entity manager, so guiModsFini must release it while
        that scene is still alive.  The shutdown GC runs after it is gone."""
        vehicle = RemoteVehicle(
            1004, _Descriptor(), {
                'publicInfo': {'team': 2, 'name': 'Bot'},
                'health': 500, 'isCrewActive': True,
                'gunAnglesPacked': 0},
            _Vector(), (0.0, 0.0, 0.0),
            types.SimpleNamespace(Vector3=_Vector, Matrix=_Matrix))
        vehicle.model = _Model()
        sound_object = mock.Mock()
        sound_module = types.ModuleType('SoundGroups')
        sound_module.g_instance = types.SimpleNamespace(
            WWgetSoundObject=mock.Mock(return_value=sound_object))
        with mock.patch.dict(sys.modules, {'SoundGroups': sound_module}):
            vehicle.appearance.engineAudition.getSoundObject(2)
        self.assertEqual(
            {2: sound_object}, vehicle.appearance.engineAudition._objects)

        vehicle.abandon_visual()

        self.assertEqual({}, vehicle.appearance.engineAudition._objects)

    def test_remote_shot_effect_contract_failure_is_not_hidden(self):
        class BrokenExtra(object):
            def stopFor(self, unused_vehicle):
                return None

            def startFor(self, unused_vehicle, unused_burst):
                raise RuntimeError('shot sound contract failed')

        descriptor = _Descriptor()
        descriptor.extrasDict = {'shoot': BrokenExtra()}
        vehicle = RemoteVehicle(
            1000, descriptor, {
                'publicInfo': {'team': 2, 'name': 'Bot'},
                'health': 500, 'isCrewActive': True,
                'gunAnglesPacked': 0},
            _Vector(), (0.0, 0.0, 0.0),
            types.SimpleNamespace(Vector3=_Vector, Matrix=_Matrix))
        vehicle.model = _Model()
        vehicle.isStarted = True
        vehicle.inWorld = True

        with self.assertRaisesRegex(
                RuntimeError, 'shot sound contract failed'):
            vehicle.showShooting(1, False)

    def test_remote_siege_callback_swaps_descriptor_and_blocks_transition_shot(self):
        descriptor = _Descriptor()
        descriptor.hasSiegeMode = True
        descriptor.siege_updates = []
        descriptor.onSiegeStateChanged = (
            lambda state: descriptor.siege_updates.append(state))
        vehicle = RemoteVehicle(
            1000, descriptor, {
                'publicInfo': {'team': 2, 'name': 'Strv'},
                'health': 500, 'isCrewActive': True,
                'gunAnglesPacked': 0},
            _Vector(), (0.0, 0.0, 0.0),
            types.SimpleNamespace(Vector3=_Vector, Matrix=_Matrix))
        vehicle.model = _Model()
        vehicle.isStarted = True
        vehicle.inWorld = True

        self.assertTrue(vehicle.onSiegeStateUpdated(1, 2.0))

        self.assertEqual([1], descriptor.siege_updates)
        self.assertEqual(1, vehicle.siegeState)
        self.assertEqual(1, vehicle.appearance.siegeState)
        self.assertFalse(vehicle.showShooting(1, False))

    def test_remote_filter_implements_1513_three_argument_broad_phase(self):
        math_module = types.SimpleNamespace(Vector3=_Vector)
        matrix = _Matrix()
        remote_filter = _RemoteFilter(
            math_module, _Vector(0.0, 0.0, 0.0), matrix)

        self.assertTrue(remote_filter.segmentMayHitEntity(
            _Vector(-30.0, 0.0, 0.0), _Vector(30.0, 0.0, 0.0), True))
        self.assertFalse(remote_filter.segmentMayHitEntity(
            _Vector(-30.0, 50.0, 0.0), _Vector(30.0, 50.0, 0.0), False))
        self.assertIs(matrix, remote_filter.stabilisedMatrix)
        self.assertIs(matrix, remote_filter.groundPlacingMatrixFiltered)

    def test_remote_collision_returns_exact_1513_nearest_tuple(self):
        vehicle = RemoteVehicle(
            1000, _Descriptor(), {
                'publicInfo': {'team': 2, 'name': 'Bot'},
                'health': 500, 'isCrewActive': True,
                'gunAnglesPacked': 0},
            _Vector(), (0.0, 0.0, 0.0),
            types.SimpleNamespace(Vector3=_Vector, Matrix=_Matrix))
        material = types.SimpleNamespace(armor=120.0)
        collision = types.SimpleNamespace(
            dist=0.25, hitAngleCos=0.75, matInfo=material,
            compName='vehicleHull')
        vehicle.collideSegmentExt = lambda start, end: [collision]

        result = vehicle.collideSegment(_Vector(), _Vector(1.0, 0.0, 0.0))

        self.assertEqual(0.25, result[0])
        self.assertEqual(0.25, result.dist)
        self.assertEqual(0.75, result.hitAngleCos)
        self.assertEqual(120.0, result.armor)
        self.assertEqual(3, len(result))

    def test_remote_shot_uses_stock_extra_recoil_and_1513_tracer(self):
        runtime = _runtime()
        original_entity = runtime.bigworld.entity
        runtime.bigworld.PyModelObstacle = mock.Mock(
            side_effect=AssertionError(
                'remote presentation must not create a second collider'))
        recoil = mock.Mock()
        assemble_recoil = mock.Mock(side_effect=lambda appearance, unused_lod:
                                    setattr(appearance, 'gunRecoil', recoil))
        setup_rotations = mock.Mock(side_effect=lambda appearance: (
            appearance.compoundModel.node(
                'turret', appearance.turretMatrix),
            appearance.compoundModel.node(
                'gun_inclination', appearance.gunMatrix)))
        runtime.model_assembler.assembleRecoil = assemble_recoil
        runtime.model_assembler.setupTurretRotations = setup_rotations
        runtime.bigworld.camera = lambda: types.SimpleNamespace(
            position=_Vector(50.0, 20.0, 30.0))
        projectiles = []

        class ProjectileMover(object):
            def __init__(self):
                self._ProjectileMover__projectiles = {}
                self.add = mock.Mock(side_effect=self._add)
                self.destroy = mock.Mock()
                self.space_ids = []
                projectiles.append(self)

            def setSpaceID(self, space_id):
                self.space_ids.append(space_id)

            def _add(self, shot_id, *unused_args):
                self._ProjectileMover__projectiles[shot_id] = object()

        class ShootExtra(object):
            index = 5

            def __init__(self):
                self.started = []
                self.stopped = []

            def stopFor(self, entity):
                data = entity.extras.pop(self.index, None)
                if data is None:
                    return False
                self.stopped.append(entity.id)
                return True

            def stop(self, data):
                entity = data['entity']
                del entity.extras[self.index]
                self.stopped.append(entity.id)
                data['entity'] = None

            def startFor(self, entity, burst_count):
                self.started.append((entity.id, burst_count))
                entity.extras[self.index] = {
                    'extra': self, 'entity': entity}
                entity.appearance.recoil()

        descriptor = _Descriptor()
        descriptor.hull.models = {'undamaged': 'hull.model'}
        descriptor.turret.models = {'undamaged': 'turret.model'}
        shoot_extra = ShootExtra()
        descriptor.extrasDict = {'shoot': shoot_extra}
        descriptor.extras = {ShootExtra.index: shoot_extra}
        descriptor.gun.burst = (3, 0.1)
        descriptor.gun.shots[0].speed = 950.0
        descriptor.gun.shots[0].gravity = 9.81
        descriptor.gun.shots[0].shell.effectsIndex = 7
        alternate_shot = types.SimpleNamespace(
            shell=types.SimpleNamespace(effectsIndex=8),
            speed=1100.0, gravity=4.0, maxDistance=720.0)
        descriptor.gun.shots.append(alternate_shot)
        items_module = types.ModuleType('items')
        items_module.vehicles = types.SimpleNamespace(
            g_cache=types.SimpleNamespace(
                shotEffects={
                    7: {'projectile': 'ap-tracer'},
                    8: {'projectile': 'he-tracer'}}))
        projectile_module = types.ModuleType('ProjectileMover')
        projectile_module.ProjectileMover = ProjectileMover

        with mock.patch.dict(sys.modules, {
                'items': items_module,
                'ProjectileMover': projectile_module}):
            factory = RemoteVehicleFactory(
                runtime.bigworld, runtime.math, runtime.model_assembler, 7)
            vehicle_id = factory.create(descriptor, {
                'publicInfo': {'team': 2, 'name': 'Bot'},
                'health': 500, 'isCrewActive': True,
                'gunAnglesPacked': 0}, _Vector(10.0, 2.0, 20.0),
                (0.0, 0.0, 0.0))
            vehicle = factory.get(vehicle_id)
            vehicle.set_aim(0.0, math.pi / 2.0, -0.1)
            vehicle._offlineLANShotIndex = 1

            battle = BattleRuntime(runtime)
            battle._remote_factory = factory
            battle._records = {
                'bot:11': {'engine_id': vehicle_id, 'local': False}}
            battle._show_shot({
                'kind': 'bot_shot', 'attacker_bot': 11,
                'shell_index': 1, 'shot_yaw': math.pi / 2.0,
                'shot_pitch': 0.1})

            self.assertEqual((3, False), vehicle.last_shot)
            self.assertEqual((True, True), vehicle.last_shot_effect)
            self.assertEqual([(vehicle_id, 3)], shoot_extra.started)
            assemble_recoil.assert_called_once_with(
                vehicle.appearance, None)
            setup_rotations.assert_called_once_with(vehicle.appearance)
            self.assertEqual([
                ('turret', vehicle.appearance.turretMatrix),
                ('gun_inclination', vehicle.appearance.gunMatrix),
            ], vehicle.model.node_bindings)
            recoil.recoil.assert_called_once_with()
            self.assertEqual(1, len(projectiles))
            self.assertEqual([7], projectiles[0].space_ids)
            projectile_args = projectiles[0].add.call_args[0]
            self.assertEqual(9, len(projectile_args))
            self.assertEqual({'projectile': 'he-tracer'}, projectile_args[1])
            self.assertEqual(4.0, projectile_args[2])
            self.assertAlmostEqual(math.cos(0.1) * 1100.0,
                                   projectile_args[4].x)
            self.assertAlmostEqual(math.sin(0.1) * 1100.0,
                                   projectile_args[4].y)
            self.assertAlmostEqual(0.0, projectile_args[4].z, places=5)
            self.assertEqual(720.0, projectile_args[6])
            self.assertEqual(vehicle_id, projectile_args[7])
            self.assertFalse(hasattr(vehicle, '_offlineLANShotYaw'))
            self.assertFalse(hasattr(vehicle, '_offlineLANShotPitch'))
            runtime.bigworld.PyModelObstacle.assert_not_called()
            self.assertIsNone(vehicle._collision_obstacle)

            factory.destroy_all()

            self.assertFalse(vehicle.showShooting(1, False))
            projectiles[0].add.assert_called_once_with(*projectile_args)

        # The teardown drains the running extra through its own stop(),
        # exactly like #1513 Vehicle.__stopExtras.
        self.assertEqual([vehicle_id], shoot_extra.stopped)
        self.assertEqual({}, vehicle.extras)
        self.assertIsNone(vehicle._collision_obstacle)
        projectiles[0].destroy.assert_called_once_with()
        self.assertEqual(runtime.bigworld.entity, original_entity)

    def test_a_bot_belt_feed_never_touches_the_vehicle_filter(self):
        """setTracksSpeed aborts the client on a filter no entity owns, so the
        feed uses only the stock PyTrackScroll pair, and the belts stay still
        because that controller has no filter implementation either."""
        runtime = _runtime()
        fashions = [types.SimpleNamespace(movementInfo=None)]
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7,
            camouflages=types.SimpleNamespace(
                prepareFashions=lambda damaged: fashions))
        vehicle_id = factory.create(_Descriptor(), {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True, 'gunAnglesPacked': 0},
            _Vector(), (0.0, 0.0, 0.0))
        vehicle = factory.get(vehicle_id)

        self.assertIsNone(factory.track_animation_error)
        self.assertIsNotNone(vehicle.track_scroll)
        self.assertTrue(vehicle.track_scroll.active)
        self.assertIs(vehicle.track_filter, vehicle.track_scroll.data)
        self.assertIs(vehicle.track_filter.movementInfo,
                      fashions[0].movementInfo)
        self.assertIs(fashions, vehicle.model.fashions)

        vehicle.update_tracks(3.0, 5.0, (2, 1))
        self.assertEqual((2, 1), vehicle.track_scroll.mode)
        self.assertEqual((3.0, 5.0), vehicle.track_scroll.external)
        # The controller's own readback still holds its constructed values,
        # which is what the exact client reported for a whole battle.
        self.assertEqual((0.0, 0.0, True, True),
                         vehicle.track_scroll_readback())

        scroll = vehicle.track_scroll
        vehicle.detach_visual()
        # The updater must be cancelled and its raw filter pointer cleared
        # before the filter reference goes.
        self.assertFalse(scroll.active)
        self.assertIsNone(scroll.data)
        self.assertIsNone(vehicle.track_scroll)
        self.assertIsNone(vehicle.track_filter)

    def test_a_teardown_after_the_engine_leaves_native_objects_alone(self):
        """game.fini resets the entity manager and clears every space before
        this mod runs, so the held compounds and filters are already freed."""
        runtime = _runtime()
        fashions = [types.SimpleNamespace(movementInfo=None)]
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7,
            camouflages=types.SimpleNamespace(
                prepareFashions=lambda damaged: fashions))
        vehicle_id = factory.create(_Descriptor(), {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True, 'gunAnglesPacked': 0},
            _Vector(), (0.0, 0.0, 0.0))
        vehicle = factory.get(vehicle_id)
        scroll = vehicle.track_scroll
        model = vehicle.model
        self.assertTrue(factory.engine_owns(vehicle.bw_entity_id))

        runtime.bigworld.entities.clear()
        self.assertFalse(factory.engine_owns(vehicle.bw_entity_id))
        self.assertTrue(factory.destroy(vehicle_id))

        self.assertIsNone(vehicle.track_scroll)
        self.assertIsNone(vehicle.track_filter)
        self.assertIsNone(vehicle.model)
        self.assertIsNone(vehicle.bw_entity)
        self.assertFalse(vehicle.inWorld)
        # Nothing native was called: the updater keeps its stale registration
        # and the compound keeps its last matrix.
        self.assertTrue(scroll.active)
        self.assertIsNotNone(scroll.data)
        self.assertIsNotNone(model.matrix)

    def test_the_belt_assembly_is_off_and_still_switchable(self):
        from gui.mods.offline_lan_0922 import config as port_config

        self.assertFalse(
            port_config.DEFAULT_CONFIG['bot_track_animation'])

    def test_a_client_without_the_belt_boundary_still_presents_bots(self):
        runtime = _runtime()
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7,
            camouflages=types.SimpleNamespace(
                prepareFashions=mock.Mock(side_effect=RuntimeError('no'))))
        vehicle_id = factory.create(_Descriptor(), {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True, 'gunAnglesPacked': 0},
            _Vector(), (0.0, 0.0, 0.0))
        vehicle = factory.get(vehicle_id)

        self.assertIsNotNone(factory.track_animation_error)
        self.assertIsNone(vehicle.track_scroll)
        self.assertTrue(factory.is_ready(vehicle_id))
        self.assertIsNotNone(vehicle.model)

    def test_observed_vehicle_state_handlers_can_read_health_and_motion(self):
        runtime = _runtime()
        vehicle = RemoteVehicle(
            1000, _Descriptor(), {
                'publicInfo': {'team': 1, 'name': 'Observed'},
                'health': 375, 'isCrewActive': True,
                'gunAnglesPacked': 0},
            _Vector(), (0.0, 0.0, 0.0), runtime.math)

        # Exact #1513 constructs all four non-player state handlers before
        # its first health poll; these fields therefore share one contract.
        self.assertFalse(vehicle.isPlayerVehicle)
        self.assertEqual(375, vehicle.health)
        self.assertEqual(0, vehicle.appearance.gear)
        self.assertEqual((0.0, 0.0), vehicle.speedInfo.value)

        vehicle.set_pose(
            _Vector(1.0, 0.0, 0.0), (0.0, 0.0, 0.0), now=10.0)
        self.assertEqual(0, vehicle.appearance.gear)
        self.assertEqual((0.0, 0.0), vehicle.speedInfo.value)
        vehicle.set_pose(
            _Vector(4.0, 0.0, 0.0), (0.0, 0.0, 0.0), now=11.0)
        self.assertEqual(1, vehicle.appearance.gear)
        self.assertEqual((3.0, 0.0), vehicle.speedInfo.value)

    def test_an_unchanged_pose_does_not_rekey_the_animation(self):
        """A bot below the render rate republishes the same pose; re-keying
        it would hold the model still and then jump on the next step."""
        runtime = _runtime()
        battle = BattleRuntime(runtime)

        first = battle._bot_pose_relax({'id': 3, 'yaw': 0.0}, 'a', 10.0)
        self.assertIsNone(first)

        self.assertIsNone(
            battle._bot_pose_relax({'id': 3, 'yaw': 0.0}, 'a', 10.02))
        self.assertIsNone(
            battle._bot_pose_relax({'id': 3, 'yaw': 0.0}, 'a', 10.04))

        relax = battle._bot_pose_relax({'id': 3, 'yaw': 0.0}, 'b', 10.06)

        # The gap is measured to the last CHANGE, not to the last frame.
        self.assertAlmostEqual(
            0.06 * battle_runtime_module.POSE_RELAX_STRETCH, relax)

    def test_same_frame_pose_changes_do_not_divide_by_zero(self):
        battle = BattleRuntime(_runtime())

        self.assertIsNone(
            battle._bot_pose_relax({'id': 3, 'yaw': 0.0}, 'a', 10.0))
        self.assertIsNone(
            battle._bot_pose_relax({'id': 3, 'yaw': 0.2}, 'b', 10.0))

        self.assertEqual(0.0, battle._bot_yaw_rates[3])
        self.assertAlmostEqual(
            0.1 * battle_runtime_module.POSE_RELAX_STRETCH,
            battle._bot_pose_relax({'id': 3, 'yaw': 0.3}, 'c', 10.1))
        self.assertAlmostEqual(1.0, battle._bot_yaw_rates[3])

    def test_same_frame_remote_track_samples_do_not_divide_by_zero(self):
        battle = BattleRuntime(_runtime())
        record = {}

        self.assertEqual(
            0.0, battle._remember_remote_track_turn(record, 0.0, 10.0))
        self.assertEqual(
            0.0, battle._remember_remote_track_turn(record, 0.2, 10.0))
        self.assertAlmostEqual(
            1.0, battle._remember_remote_track_turn(record, 0.3, 10.1))

    def test_a_bot_pose_is_animated_without_allocating_per_pose(self):
        """OfflineEntity declares an empty <Volatile/>, so no WGVehicleFilter
        can ever interpolate for a bot; the compound animates instead, and
        rekeying it must not allocate a native object per pose."""
        runtime = _runtime()
        vehicle = RemoteVehicle(
            1000, _Descriptor(), {
                'publicInfo': {'team': 2, 'name': 'Bot'},
                'health': 500, 'isCrewActive': True, 'gunAnglesPacked': 0},
            _Vector(), (0.0, 0.0, 0.0), runtime.math)
        model = _Model()
        vehicle.attach_visual(types.SimpleNamespace(model=None), 7, model)
        self.assertIs(vehicle._animation, model.matrix)
        allocations = remote_vehicle_module.pose_animation_writes()

        vehicle.set_pose(_Vector(10.0, 0.0, 20.0), (0.0, 0.0, 1.0),
                         relax_time=0.05, now=100.0)

        keyframes = vehicle._animation.keyframes
        self.assertEqual(2, len(keyframes))
        self.assertEqual(0.0, keyframes[0][0])
        self.assertEqual(0.05, keyframes[1][0])
        self.assertEqual(0.0, vehicle._animation.time)
        # The engine interpolates between the vehicle's own two matrices.
        self.assertIs(vehicle._key_from, keyframes[0][1])
        self.assertIs(vehicle._key_to, keyframes[1][1])

        vehicle.set_pose(_Vector(20.0, 0.0, 40.0), (0.0, 0.0, 2.0),
                         relax_time=0.05, now=100.05)

        self.assertIs(vehicle._key_from, vehicle._animation.keyframes[0][1])
        self.assertEqual(
            allocations, remote_vehicle_module.pose_animation_writes())

    def test_a_rekey_starts_from_the_drawn_pose_not_the_last_target(self):
        runtime = _runtime()
        vehicle = RemoteVehicle(
            1000, _Descriptor(), {
                'publicInfo': {'team': 2, 'name': 'Bot'},
                'health': 500, 'isCrewActive': True, 'gunAnglesPacked': 0},
            _Vector(), (0.0, 0.0, 0.0), runtime.math)
        vehicle.attach_visual(types.SimpleNamespace(model=None), 7, _Model())
        vehicle.set_pose(_Vector(10.0, 0.0, 0.0), (0.0, 0.0, 0.0),
                         relax_time=0.10, now=100.0)

        # Half way through the first ease a new pose arrives; the animation
        # must continue from the middle rather than jump back to the start.
        vehicle.set_pose(_Vector(20.0, 0.0, 0.0), (0.0, 0.0, 0.0),
                         relax_time=0.10, now=100.05)

        self.assertAlmostEqual(5.0, vehicle._render_pose[0])

    def test_a_pose_without_a_relax_time_is_applied_directly(self):
        runtime = _runtime()
        vehicle = RemoteVehicle(
            1000, _Descriptor(), {
                'publicInfo': {'team': 2, 'name': 'Bot'},
                'health': 500, 'isCrewActive': True, 'gunAnglesPacked': 0},
            _Vector(), (0.0, 0.0, 0.0), runtime.math)
        vehicle.attach_visual(types.SimpleNamespace(model=None), 7, _Model())

        vehicle.set_pose(_Vector(1.0, 0.0, 2.0), (0.0, 0.0, 0.0))

        self.assertEqual((1.0, 0.0, 2.0), vehicle._render_pose[:3])
        # Both keys carry the new pose, so the animation cannot blend away
        # from it; the interval stays positive because 0/0 is not a factor.
        keyframes = vehicle._animation.keyframes
        self.assertGreater(keyframes[1][0], keyframes[0][0])
        self.assertEqual(keyframes[0][1].translation,
                         keyframes[1][1].translation)

    def test_destroyed_remote_vehicle_retains_loaded_compound_once(self):
        runtime = _runtime()
        states = []
        original_assembler = runtime.model_assembler.prepareCompoundAssembler

        def record_assembler(descriptor, state, space, flag):
            states.append(state)
            return original_assembler(descriptor, state, space, flag)

        runtime.model_assembler.prepareCompoundAssembler = record_assembler
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        vehicle_id = factory.create(_Descriptor(), {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True, 'gunAnglesPacked': 0},
            _Vector(), (0.0, 0.0, 0.0))
        vehicle = factory.get(vehicle_id)
        undamaged = vehicle.model

        self.assertTrue(factory.request_wreck(vehicle_id))
        self.assertFalse(factory.request_wreck(vehicle_id))

        self.assertEqual(['undamaged'], states)
        self.assertIs(undamaged, vehicle.model)
        self.assertIs(vehicle.model, vehicle.bw_entity.model)
        self.assertTrue(vehicle._wreck_retained)
        self.assertFalse(
            vehicle.appearance.damageState.isCurrentModelDamaged)
        self.assertTrue(factory.is_ready(vehicle_id))

    def test_wreck_parts_are_deduplicated_and_resource_refs_are_retained(self):
        runtime = _runtime()
        runtime.model_assembler.getPartModelsFromDesc = (
            lambda descriptor, state: (
                'shared-destroyed-chassis.model',
                'shared-destroyed-chassis.model',
                '%s-destroyed-hull.model' % descriptor.name))
        requests = []
        runtime.bigworld.loadResourceListBG = (
            lambda paths, callback: requests.append((paths, callback)))
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7,
            prewarm_wreck_resources=True)
        first = _Descriptor('ussr:R11_MS-1')
        second = _Descriptor('ussr:R04_T-34')

        factory.prepare_descriptor(first)
        factory.prepare_descriptor(second)

        self.assertEqual(
            ('shared-destroyed-chassis.model',
             'ussr:R11_MS-1-destroyed-hull.model'), requests[0][0])
        self.assertEqual(
            ('ussr:R04_T-34-destroyed-hull.model',), requests[1][0])
        refs = types.SimpleNamespace(failedIDs=())
        requests[0][1](refs)
        requests[1][1](types.SimpleNamespace(failedIDs=()))
        self.assertIs(refs, factory._wreck_resource_refs[0])
        self.assertTrue(factory._wreck_resources_ready(first))
        self.assertTrue(factory._wreck_resources_ready(second))
        factory.destroy_all()
        self.assertIsNone(factory._wreck_resource_refs)

    def test_hot_wreck_parts_swap_to_the_destroyed_compound(self):
        runtime = _runtime()
        states = []
        runtime.model_assembler.getPartModelsFromDesc = (
            lambda descriptor, state: tuple(
                '%s-%s-%s.model' % (descriptor.name, state, part)
                for part in ('chassis', 'hull', 'turret', 'gun')))

        def prepare(descriptor, state, unused_space, unused_detached):
            states.append(state)
            return types.SimpleNamespace(name=descriptor.name, state=state)

        class ResourceRefs(dict):
            failedIDs = ()

        def load(resources, callback):
            if isinstance(resources[0], str):
                callback(ResourceRefs())
                return
            callback(ResourceRefs({resources[0].name: _Model()}))

        runtime.model_assembler.prepareCompoundAssembler = prepare
        runtime.bigworld.loadResourceListBG = load
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7,
            prewarm_wreck_resources=True)
        descriptor = _Descriptor()
        vehicle_id = factory.create(descriptor, {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True, 'gunAnglesPacked': 0},
            _Vector(), (0.0, 0.0, 0.0))
        vehicle = factory.get(vehicle_id)
        undamaged = vehicle.model

        self.assertTrue(factory.request_wreck(vehicle_id))

        self.assertEqual(['undamaged', 'destroyed'], states)
        self.assertIsNot(undamaged, vehicle.model)
        self.assertIs(vehicle.model, vehicle.bw_entity.model)
        self.assertTrue(
            vehicle.appearance.damageState.isCurrentModelDamaged)
        self.assertFalse(factory.request_wreck(vehicle_id))
        factory.destroy_all()

    def test_early_death_swaps_after_its_pending_prewarm_finishes(self):
        runtime = _runtime()
        states = []
        pending = []
        runtime.model_assembler.getPartModelsFromDesc = (
            lambda descriptor, state: ('cold-destroyed.model',))

        def prepare(descriptor, state, unused_space, unused_detached):
            states.append(state)
            return types.SimpleNamespace(name=descriptor.name, state=state)

        class ResourceRefs(dict):
            failedIDs = ()

        def load(resources, callback):
            if isinstance(resources[0], str):
                pending.append(callback)
                return
            callback(ResourceRefs({resources[0].name: _Model()}))

        runtime.model_assembler.prepareCompoundAssembler = prepare
        runtime.bigworld.loadResourceListBG = load
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7,
            prewarm_wreck_resources=True)
        descriptor = _Descriptor()
        vehicle_id = factory.create(descriptor, {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True, 'gunAnglesPacked': 0},
            _Vector(), (0.0, 0.0, 0.0))
        vehicle = factory.get(vehicle_id)
        undamaged = vehicle.model

        self.assertTrue(factory.request_wreck(vehicle_id))
        self.assertEqual(['undamaged'], states)
        self.assertIs(undamaged, vehicle.model)
        pending[0](ResourceRefs())

        self.assertEqual(['undamaged', 'destroyed'], states)
        self.assertIsNot(undamaged, vehicle.model)
        self.assertTrue(vehicle._wreck_retained)
        self.assertTrue(
            vehicle.appearance.damageState.isCurrentModelDamaged)
        factory.destroy_all()

    def test_failed_wreck_prewarm_permanently_keeps_the_full_compound(self):
        runtime = _runtime()
        states = []
        pending = []
        runtime.model_assembler.getPartModelsFromDesc = (
            lambda descriptor, state: ('missing-destroyed.model',))

        def prepare(descriptor, state, unused_space, unused_detached):
            states.append(state)
            return types.SimpleNamespace(name=descriptor.name, state=state)

        class ResourceRefs(dict):
            failedIDs = ('missing-destroyed.model',)

        def load(resources, callback):
            if isinstance(resources[0], str):
                pending.append(callback)
                return
            callback(ResourceRefs({resources[0].name: _Model()}))

        runtime.model_assembler.prepareCompoundAssembler = prepare
        runtime.bigworld.loadResourceListBG = load
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7,
            prewarm_wreck_resources=True)
        descriptor = _Descriptor()
        vehicle_id = factory.create(descriptor, {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True, 'gunAnglesPacked': 0},
            _Vector(), (0.0, 0.0, 0.0))
        vehicle = factory.get(vehicle_id)
        undamaged = vehicle.model

        self.assertTrue(factory.request_wreck(vehicle_id))
        pending[0](ResourceRefs())

        self.assertEqual(['undamaged'], states)
        self.assertIs(undamaged, vehicle.model)
        self.assertTrue(vehicle._wreck_retained)
        self.assertFalse(
            vehicle.appearance.damageState.isCurrentModelDamaged)
        self.assertEqual(set(), factory._wreck_waiting_entities)
        factory.destroy_all()

    def test_abandoned_wreck_prewarm_ignores_its_late_callback(self):
        runtime = _runtime()
        pending = []
        runtime.model_assembler.getPartModelsFromDesc = (
            lambda descriptor, state: ('late-destroyed.model',))
        runtime.bigworld.loadResourceListBG = (
            lambda paths, callback: pending.append(callback))
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7,
            prewarm_wreck_resources=True)
        descriptor = _Descriptor()

        factory.prepare_descriptor(descriptor)
        self.assertEqual(1, factory.wreck_prewarm_pending_count())
        self.assertTrue(factory.abandon_pending_wreck_prewarm())
        self.assertEqual(0, factory.wreck_prewarm_pending_count())

        pending[0](types.SimpleNamespace(failedIDs=()))

        self.assertFalse(factory._wreck_resources_ready(descriptor))
        self.assertEqual([], factory._wreck_resource_refs)
        factory.destroy_all()

    def test_remote_shot_cleanup_failure_still_restores_entity_owners(self):
        runtime = _runtime()
        original_entity = runtime.bigworld.entity
        original_entities = runtime.bigworld.entities
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        factory.create(_Descriptor(), {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True, 'gunAnglesPacked': 0},
            _Vector(), (0.0, 0.0, 0.0))
        factory._shot_presenter._mover = types.SimpleNamespace(
            destroy=mock.Mock(side_effect=RuntimeError('mover failed')))

        with self.assertRaisesRegex(RuntimeError, 'mover failed'):
            factory.destroy_all()

        self.assertEqual({}, factory._vehicles)
        self.assertEqual(runtime.bigworld.entity, original_entity)
        self.assertIs(runtime.bigworld.entities, original_entities)

    def test_compound_model_uses_separate_synthetic_vehicle_identity(self):
        runtime = _runtime()
        bigworld = runtime.bigworld
        original_entity = bigworld.entity
        original_entities = bigworld.entities
        factory = RemoteVehicleFactory(
            bigworld, runtime.math, runtime.model_assembler, 7)
        descriptor = _Descriptor()
        properties = {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True,
            'gunAnglesPacked': 0}

        vehicle_id = factory.create(
            descriptor, properties, _Vector(10.0, 2.0, 30.0),
            (0.0, 0.0, 0.5))
        vehicle = factory.get(vehicle_id)

        self.assertEqual(1000, vehicle_id)
        self.assertTrue(factory.is_ready(vehicle_id))
        self.assertIsNone(bigworld.entity(vehicle_id))
        self.assertTrue(vehicle._offlineLANPresentation)
        self.assertEqual('Vehicle', vehicle.__class__.__name__)
        self.assertNotEqual(vehicle_id, vehicle.bw_entity_id)
        self.assertNotIn(vehicle_id, bigworld.entities)
        self.assertIsNone(bigworld.entities.get(vehicle_id))
        vehicle._spot_visible = True
        self.assertIs(vehicle, bigworld.entity(vehicle_id))
        self.assertIs(vehicle, bigworld.entities[vehicle_id])
        self.assertIs(vehicle, bigworld.entities.get(vehicle_id))
        vehicle._spot_visible = False
        self.assertIsNone(bigworld.entity(vehicle_id))
        self.assertNotIn(vehicle_id, bigworld.entities)
        self.assertIsNone(bigworld.entities.get(vehicle_id))
        vehicle._spot_visible = True
        vehicle.health = 0
        vehicle.isAlive.value = False
        self.assertIsNone(bigworld.entity(vehicle_id))
        self.assertNotIn(vehicle_id, bigworld.entities)
        vehicle.health = 500
        vehicle.isAlive.value = True
        self.assertIs(vehicle, bigworld.entity(vehicle_id))
        self.assertIs(vehicle._animation, vehicle.model.matrix)
        self.assertEqual(
            (10.0, 2.0, 30.0), tuple(vehicle.matrix.translation))
        self.assertEqual(0.5, vehicle.matrix.yaw)
        self.assertEqual(
            (0.0, 0.0, 0.5),
            bigworld.created_offline_entities[-1]['rotation'])

        vehicle.set_pose(_Vector(20.0, 3.0, 40.0), (0.0, 0.0, 1.0))
        self.assertEqual((20.0, 3.0, 40.0), tuple(vehicle.position))
        self.assertIs(vehicle._animation, vehicle.model.matrix)
        self.assertEqual(
            (20.0, 3.0, 40.0), tuple(vehicle.matrix.translation))
        self.assertEqual(1.0, vehicle.matrix.yaw)

        visual_id = vehicle.bw_entity_id
        visual = bigworld.entity(visual_id)
        model = vehicle.model
        factory.destroy_all()
        self.assertIsNone(bigworld.entity(visual_id))
        self.assertNotIn(vehicle_id, bigworld.entities)
        self.assertIs(bigworld.entities, original_entities)
        self.assertEqual(original_entity, bigworld.entity)
        self.assertIsNone(visual.model)
        self.assertIsNot(model.matrix, vehicle.matrix)
        self.assertEqual((0.0, 0.0, 0.0), tuple(model.matrix.translation))

    def test_manual_target_outline_uses_exact_1513_edge_api(self):
        runtime = _runtime()
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        vehicle_id = factory.create(_Descriptor(), {
            'publicInfo': {'team': 1, 'name': 'Ally'},
            'health': 500, 'isCrewActive': True,
            'gunAnglesPacked': 0}, _Vector(0.0, 0.0, 20.0),
            (0.0, 0.0, 0.0))
        vehicle = factory.get(vehicle_id)
        vehicle.collideSegmentExt = lambda start, end: (
            types.SimpleNamespace(dist=20.0),)
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._remote_factory = factory
        # SpeedTree leaf volumes affect camouflage, not mask-128 static-world
        # occlusion, so a clear mouse ray still reaches the vehicle.
        battle._foliage = types.SimpleNamespace(
            camouflage_bonus=lambda *unused: 0.60)
        battle._records = {
            'bot:11': {'engine_id': vehicle_id, 'local': False,
                       'ready': True}}

        battle._update_target_outline(1.0)

        self.assertEqual(
            [(vehicle.bw_entity, 2, 0, False)],
            runtime.bigworld.edge_adds)
        battle._clear_target_outline()
        self.assertEqual([vehicle.bw_entity], runtime.bigworld.edge_removes)
        factory.destroy_all()

    def test_scenery_between_the_mouse_ray_and_enemy_blocks_the_outline(self):
        """The cursor ray, rather than the physical gun line, owns occlusion."""
        runtime = _runtime()
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        vehicle_id = factory.create(_Descriptor(), {
            'publicInfo': {'team': 2, 'name': 'Churchill'},
            'health': 539, 'isCrewActive': True,
            'gunAnglesPacked': 0}, _Vector(0.0, 0.0, 300.0),
            (0.0, 0.0, 0.0))
        vehicle = factory.get(vehicle_id)
        vehicle.collideSegmentExt = lambda start, end: (
            types.SimpleNamespace(dist=300.0),)

        def refuse_gun_ray():
            raise AssertionError('the outline must not read the gun ray')

        runtime.bigworld.avatar.gunRotator.getCurShotPosition = refuse_gun_ray
        runtime.bigworld.wg_collideSegment = (
            lambda space, start, end, mask: (_Vector(0.0, 0.0, 5.0),))
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._remote_factory = factory
        battle._records = {
            'bot:11': {'engine_id': vehicle_id, 'local': False,
                       'ready': True, 'spot_visible': True}}

        battle._update_target_outline(1.0)

        self.assertEqual([], runtime.bigworld.edge_adds)
        self.assertIsNone(battle._outlined_engine_id)
        self.assertIn('is behind scenery', battle._outline_report)
        factory.destroy_all()

    def test_a_retained_wreck_blocks_an_enemy_outline(self):
        runtime = _runtime()
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        wreck_id = factory.create(_Descriptor(), {
            'publicInfo': {'team': 2, 'name': 'Wreck'},
            'health': 0, 'isCrewActive': False,
            'gunAnglesPacked': 0}, _Vector(0.0, 0.0, 100.0),
            (0.0, 0.0, 0.0))
        target_id = factory.create(_Descriptor(), {
            'publicInfo': {'team': 2, 'name': 'Target'},
            'health': 500, 'isCrewActive': True,
            'gunAnglesPacked': 0}, _Vector(0.0, 0.0, 300.0),
            (0.0, 0.0, 0.0))
        wreck = factory.get(wreck_id)
        target = factory.get(target_id)
        wreck.collideSegmentExt = lambda start, end: (
            types.SimpleNamespace(dist=100.0),)
        target.collideSegmentExt = lambda start, end: (
            types.SimpleNamespace(dist=300.0),)
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._remote_factory = factory
        battle._records = {
            'bot:10': {
                'engine_id': wreck_id, 'local': False, 'ready': True,
                'state': {'health': 0, 'alive': False}},
            'bot:11': {
                'engine_id': target_id, 'local': False, 'ready': True,
                'spot_visible': True,
                'state': {'health': 500, 'alive': True}},
        }

        battle._update_target_outline(1.0)

        self.assertEqual([], runtime.bigworld.edge_adds)
        self.assertIsNone(battle._outlined_engine_id)
        self.assertIn('is behind a wreck', battle._outline_report)
        factory.destroy_all()

    def test_a_new_target_removes_the_previous_outline_before_adding(self):
        runtime = _runtime()
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        first_id = factory.create(_Descriptor(), {
            'publicInfo': {'team': 2, 'name': 'First'},
            'health': 500, 'isCrewActive': True,
            'gunAnglesPacked': 0}, _Vector(0.0, 0.0, 100.0),
            (0.0, 0.0, 0.0))
        second_id = factory.create(_Descriptor(), {
            'publicInfo': {'team': 2, 'name': 'Second'},
            'health': 500, 'isCrewActive': True,
            'gunAnglesPacked': 0}, _Vector(60.0, 0.0, 100.0),
            (0.0, 0.0, 0.0))
        first = factory.get(first_id)
        second = factory.get(second_id)
        aimed = [first_id]
        for vehicle_id, vehicle in ((first_id, first), (second_id, second)):
            vehicle.collideSegmentExt = (
                lambda start, end, key=vehicle_id: (
                    (types.SimpleNamespace(dist=100.0),)
                    if key == aimed[0] else ()))
        events = []
        runtime.bigworld.wgAddEdgeDetectEntity = (
            lambda entity, color, group, behind: events.append(
                ('add', entity, color)))
        runtime.bigworld.wgDelEdgeDetectEntity = (
            lambda entity: events.append(('del', entity)))
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._remote_factory = factory
        battle._records = {
            'bot:11': {'engine_id': first_id, 'local': False,
                       'ready': True, 'spot_visible': True},
            'bot:12': {'engine_id': second_id, 'local': False,
                       'ready': True, 'spot_visible': True}}

        battle._update_target_outline(1.0)
        first_entity = first.bw_entity
        aimed[0] = second_id
        battle._update_target_outline(2.0)

        self.assertEqual(
            [('add', first_entity, 1), ('del', first_entity),
             ('add', second.bw_entity, 1)], events)
        self.assertEqual(second_id, battle._outlined_engine_id)
        factory.destroy_all()

    def test_a_cursor_beside_the_hull_outlines_nothing(self):
        """#1513 sets selectionFovDegrees=1.0 together with
        skeletonCheckEnabled=True, so the model decides.  The hull bounding
        box circumscribes the silhouette: 0.7 degrees off at 266 m sits inside
        that box while the cursor is still beside the tank."""
        runtime = _runtime()
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        vehicle_id = factory.create(_Descriptor(), {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True,
            'gunAnglesPacked': 0}, _Vector(3.25, 0.0, 266.0),
            (0.0, 0.0, 0.0))
        vehicle = factory.get(vehicle_id)
        vehicle.collideSegmentExt = lambda start, end: ()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._remote_factory = factory
        battle._records = {
            'bot:11': {'engine_id': vehicle_id, 'local': False,
                       'ready': True, 'spot_visible': True}}

        battle._update_target_outline(1.0)

        self.assertEqual([], runtime.bigworld.edge_adds)
        self.assertIsNone(battle._outlined_engine_id)
        factory.destroy_all()

    def test_the_outline_falls_back_to_the_inscribed_hull_width(self):
        """Without the exact per-part test the cursor must still clear the
        narrowest hull dimension, not the bounding box diagonal."""
        runtime = _runtime()
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        near_id = factory.create(_Descriptor(), {
            'publicInfo': {'team': 2, 'name': 'Near'},
            'health': 500, 'isCrewActive': True,
            'gunAnglesPacked': 0}, _Vector(3.25, 0.0, 266.0),
            (0.0, 0.0, 0.0))
        far_id = factory.create(_Descriptor(), {
            'publicInfo': {'team': 2, 'name': 'Far'},
            'health': 500, 'isCrewActive': True,
            'gunAnglesPacked': 0}, _Vector(8.0, 0.0, 266.0),
            (0.0, 0.0, 0.0))
        near = factory.get(near_id)
        far = factory.get(far_id)
        near.collideSegmentExt = None
        far.collideSegmentExt = None
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._remote_factory = factory
        battle._records = {
            'bot:11': {'engine_id': near_id, 'local': False,
                       'ready': True, 'spot_visible': True},
            'bot:12': {'engine_id': far_id, 'local': False,
                       'ready': True, 'spot_visible': True}}

        battle._update_target_outline(1.0)

        self.assertEqual([(near.bw_entity, 1, 0, False)],
                         runtime.bigworld.edge_adds)
        self.assertEqual(near_id, battle._outlined_engine_id)
        self.assertIsNot(far.bw_entity, None)
        factory.destroy_all()

    def test_an_unchanged_target_issues_no_edge_call(self):
        """Every add costs a delete, so an unchanged choice must do neither.
        Adding and deleting the same outline each pass leaves a flicker no
        player can see."""
        runtime = _runtime()
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        vehicle_id = factory.create(_Descriptor(), {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True,
            'gunAnglesPacked': 0}, _Vector(0.0, 0.0, 20.0),
            (0.0, 0.0, 0.0))
        vehicle = factory.get(vehicle_id)
        vehicle.collideSegmentExt = lambda start, end: (
            types.SimpleNamespace(dist=20.0),)
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._remote_factory = factory
        battle._records = {
            'bot:11': {'engine_id': vehicle_id, 'local': False,
                       'ready': True, 'spot_visible': True}}

        for tick in range(1, 8):
            battle._update_target_outline(float(tick))

        self.assertEqual(1, len(runtime.bigworld.edge_adds))
        self.assertEqual([], runtime.bigworld.edge_removes)
        self.assertEqual(vehicle_id, battle._outlined_engine_id)
        factory.destroy_all()

    def test_the_outline_reports_why_it_declined_a_candidate(self):
        runtime = _runtime()
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        vehicle_id = factory.create(_Descriptor(), {
            'publicInfo': {'team': 2, 'name': 'Churchill'},
            'health': 539, 'isCrewActive': True,
            'gunAnglesPacked': 0}, _Vector(300.0, 0.0, 300.0),
            (0.0, 0.0, 0.0))
        vehicle = factory.get(vehicle_id)
        vehicle.collideSegmentExt = lambda start, end: ()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._remote_factory = factory
        battle._records = {
            'bot:11': {'engine_id': vehicle_id, 'local': False,
                       'ready': True, 'spot_visible': True}}

        battle._update_target_outline(1.0)

        self.assertEqual([], runtime.bigworld.edge_adds)
        # 45 degrees to the entity origin, less the half-angle its own hull
        # subtends at 424 m.
        self.assertIn('44.5 deg off the cursor', battle._outline_report)
        self.assertIn('424 m', battle._outline_report)
        factory.destroy_all()

    def test_the_outline_leaves_the_exact_entity_that_received_it(self):
        """Highlighter removes the edge from the entity it added, so the port
        must too."""
        runtime = _runtime()
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        vehicle_id = factory.create(_Descriptor(), {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True,
            'gunAnglesPacked': 0}, _Vector(0.0, 0.0, 20.0),
            (0.0, 0.0, 0.0))
        vehicle = factory.get(vehicle_id)
        vehicle.collideSegmentExt = lambda start, end: (
            types.SimpleNamespace(dist=20.0),)
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._remote_factory = factory
        battle._records = {
            'bot:11': {'engine_id': vehicle_id, 'local': False,
                       'ready': True, 'spot_visible': True}}

        battle._update_target_outline(1.0)
        outlined = vehicle.bw_entity
        self.assertEqual([(outlined, 1, 0, False)],
                         runtime.bigworld.edge_adds)

        self.assertTrue(battle._clear_target_outline())

        self.assertEqual([outlined], runtime.bigworld.edge_removes)
        self.assertIsNone(battle._outlined_engine_id)
        factory.destroy_all()

    def test_the_engine_lookup_never_asks_python_for_membership(self):
        """#1513 PyEntities exposes only __getitem__, __len__, get, has_key,
        keys, items and values.  It has no sq_contains and no tp_iter, so
        `id in BigWorld.entities` raises TypeError and every caller of
        engine_owns would silently read False."""

        class _PyEntities(object):

            def __init__(self, rows):
                self._rows = dict(rows)

            def __getitem__(self, key):
                return self._rows[key]

            def __len__(self):
                return len(self._rows)

            def get(self, key, default=None):
                return self._rows.get(key, default)

            def keys(self):
                return list(self._rows.keys())

            def __contains__(self, unused_key):
                raise TypeError('PyEntities does not support membership')

        runtime = _runtime()
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        vehicle_id = factory.create(_Descriptor(), {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True,
            'gunAnglesPacked': 0}, _Vector(0.0, 0.0, 20.0),
            (0.0, 0.0, 0.0))
        vehicle = factory.get(vehicle_id)
        engine_entities = factory._original_entities
        present = _PyEntities({vehicle.bw_entity_id: vehicle.bw_entity})

        factory._original_entities = present
        self.assertTrue(factory.engine_owns(vehicle.bw_entity_id))
        self.assertTrue(factory.engine_active())

        factory._original_entities = _PyEntities({})
        self.assertFalse(factory.engine_owns(vehicle.bw_entity_id))
        self.assertFalse(factory.engine_active())

        # The view the port installs over BigWorld.entities wraps that same
        # object, so its own membership test cannot use `in` either.
        view = runtime.bigworld.entities
        view._original = present
        self.assertIn(vehicle.bw_entity_id, view)
        self.assertNotIn(999999, view)
        vehicle._postmortem_visible = True
        self.assertIn(vehicle_id, view.keys())
        vehicle._postmortem_visible = False

        view._original = engine_entities
        factory._original_entities = engine_entities
        factory.destroy_all()

    def test_fallback_engine_active_checks_every_owned_entity(self):
        runtime = _runtime()
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        factory._vehicles = {
            1: types.SimpleNamespace(bw_entity_id=101),
            2: types.SimpleNamespace(bw_entity_id=102),
        }
        factory._original_entities = {102: object()}

        self.assertTrue(factory.engine_active())

        factory._vehicles = {}
        self.assertFalse(factory.engine_active())
        factory.destroy_all()

    def test_a_replaced_compound_is_a_lifecycle_error_not_cleanup(self):
        """wgDelEdgeDetectEntity resolves the drawer key from the entity's
        current compound.  A removal issued after that compound changed
        deletes nothing.  Disabling later outlines cannot repair that entry."""
        runtime = _runtime()
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        vehicle_id = factory.create(_Descriptor(), {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True,
            'gunAnglesPacked': 0}, _Vector(0.0, 0.0, 20.0),
            (0.0, 0.0, 0.0))
        vehicle = factory.get(vehicle_id)
        vehicle.collideSegmentExt = lambda start, end: (
            types.SimpleNamespace(dist=20.0),)
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._remote_factory = factory
        battle._records = {
            'bot:11': {'engine_id': vehicle_id, 'local': False,
                       'ready': True, 'spot_visible': True}}

        battle._update_target_outline(1.0)
        self.assertEqual(1, len(runtime.bigworld.edge_adds))
        vehicle.model = _Model()

        with self.assertRaisesRegex(
                RuntimeError, 'changed its compound before edge removal'):
            battle._clear_target_outline()

        self.assertEqual([], runtime.bigworld.edge_removes)
        self.assertFalse(battle._outline_blocked)
        factory.destroy_all()

    def test_a_control_mode_change_drops_the_outline(self):
        """AvatarInputHandler.onControlModeChanged clears BigWorld.target, and
        the engine then reaches targetBlur, which removes the edge."""
        runtime = _runtime()
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        vehicle_id = factory.create(_Descriptor(), {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True,
            'gunAnglesPacked': 0}, _Vector(0.0, 0.0, 20.0),
            (0.0, 0.0, 0.0))
        vehicle = factory.get(vehicle_id)
        vehicle.collideSegmentExt = lambda start, end: (
            types.SimpleNamespace(dist=20.0),)
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._remote_factory = factory
        battle._records = {
            'bot:11': {'engine_id': vehicle_id, 'local': False,
                       'ready': True, 'spot_visible': True}}

        battle._update_target_outline(1.0)
        outlined = vehicle.bw_entity
        battle._on_control_mode_changed(None, 'arcade')

        self.assertEqual([outlined], runtime.bigworld.edge_removes)
        self.assertIsNone(battle._outlined_engine_id)
        factory.destroy_all()

    def test_the_outline_goes_when_the_cursor_leaves_the_model(self):
        """Retail re-runs the skeleton check every pass, so a target 20
        degrees off the cursor loses its outline even though it is well
        inside the 80 degree deselection cone."""
        runtime = _runtime()
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        vehicle_id = factory.create(_Descriptor(), {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True,
            'gunAnglesPacked': 0}, _Vector(36.397, 0.0, 100.0),
            (0.0, 0.0, 0.0))
        vehicle = factory.get(vehicle_id)
        hits = [True]
        vehicle.collideSegmentExt = lambda start, end: (
            (types.SimpleNamespace(dist=100.0),) if hits[0] else ())
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._remote_factory = factory
        battle._records = {
            'bot:11': {'engine_id': vehicle_id, 'local': False,
                       'ready': True, 'spot_visible': True}}

        battle._update_target_outline(1.0)
        outlined = vehicle.bw_entity
        hits[0] = False
        battle._update_target_outline(2.0)

        self.assertEqual([(outlined, 1, 0, False)],
                         runtime.bigworld.edge_adds)
        self.assertEqual([outlined], runtime.bigworld.edge_removes)
        self.assertIsNone(battle._outlined_engine_id)
        self.assertIn('is not under the cursor', battle._outline_report)
        factory.destroy_all()

    def test_an_ineligible_held_target_loses_its_outline(self):
        """Retail drops the target when it stops being eligible, not only when
        the cursor leaves it."""
        runtime = _runtime()
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        vehicle_id = factory.create(_Descriptor(), {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True,
            'gunAnglesPacked': 0}, _Vector(36.397, 0.0, 100.0),
            (0.0, 0.0, 0.0))
        vehicle = factory.get(vehicle_id)
        hits = [True]
        vehicle.collideSegmentExt = lambda start, end: (
            (types.SimpleNamespace(dist=100.0),) if hits[0] else ())
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._remote_factory = factory
        record = {'engine_id': vehicle_id, 'local': False,
                  'ready': True, 'spot_visible': True}
        battle._records = {'bot:11': record}

        battle._update_target_outline(1.0)
        outlined = vehicle.bw_entity
        hits[0] = False
        record['spot_visible'] = False
        battle._update_target_outline(2.0)

        self.assertEqual([outlined], runtime.bigworld.edge_removes)
        self.assertIsNone(battle._outlined_engine_id)
        self.assertIn('is not spotted', battle._outline_report)
        factory.destroy_all()

    def test_the_wreck_swap_detaches_the_compound_before_it_moves(self):
        """CompoundAppearance.deactivate clears entity.model first and only
        then points the released compound at an identity matrix."""
        runtime = _runtime()
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        vehicle_id = factory.create(_Descriptor(), {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True,
            'gunAnglesPacked': 0}, _Vector(0.0, 0.0, 20.0),
            (0.0, 0.0, 0.0))
        vehicle = factory.get(vehicle_id)
        order = []
        vehicle.model = _Watched(vehicle.model, 'compound', order)
        entity = vehicle.bw_entity
        vehicle.bw_entity = _Watched(entity, 'entity', order)

        vehicle.attach_wreck_model(_Model())

        self.assertLess(order.index(('entity', 'model', True)),
                        order.index(('compound', 'matrix', False)))
        self.assertLess(order.index(('compound', 'matrix', False)),
                        order.index(('entity', 'model', False)))
        vehicle.bw_entity = entity
        factory.destroy_all()

    def test_detach_visual_detaches_the_compound_before_it_moves(self):
        """The same order retires a visual: the entity releases the compound
        before the compound loses its live matrix provider."""
        runtime = _runtime()
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        vehicle_id = factory.create(_Descriptor(), {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True,
            'gunAnglesPacked': 0}, _Vector(0.0, 0.0, 20.0),
            (0.0, 0.0, 0.0))
        vehicle = factory.get(vehicle_id)
        order = []
        vehicle.model = _Watched(vehicle.model, 'compound', order)
        vehicle.bw_entity = _Watched(vehicle.bw_entity, 'entity', order)

        vehicle.detach_visual()

        self.assertLess(order.index(('entity', 'model', True)),
                        order.index(('compound', 'matrix', False)))

    def test_a_killed_bot_clears_outline_and_retains_its_compound(self):
        """Death styling is independent from the retained cover model."""
        runtime = _runtime()
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        descriptor = _Descriptor()
        vehicle_id = factory.create(descriptor, {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True,
            'gunAnglesPacked': 0}, _Vector(0.0, 0.0, 20.0),
            (0.0, 0.0, 0.0))
        vehicle = factory.get(vehicle_id)
        vehicle.collideSegmentExt = lambda start, end: (
            types.SimpleNamespace(dist=20.0),)
        loads = []
        runtime.bigworld.loadResourceListBG = (
            lambda assemblers, callback: loads.append((assemblers, callback)))
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        battle._remote_factory = factory
        record = {'engine_id': vehicle_id, 'local': False, 'ready': True,
                  'presentation': True,
                  'state': {'team': 2, 'health': 500}}
        battle._records = {'bot:11': record}
        battle._update_target_outline(1.0)
        outlined = vehicle.bw_entity
        undamaged = vehicle.model
        self.assertEqual([(outlined, 1, 0, False)],
                         runtime.bigworld.edge_adds)

        battle._apply_health(record, {'health': 0, 'alive': False})

        self.assertEqual([outlined], runtime.bigworld.edge_removes)
        self.assertIsNone(battle._outlined_engine_id)
        self.assertEqual([], loads)
        self.assertIs(undamaged, vehicle.model)
        self.assertIs(vehicle.model, vehicle.bw_entity.model)
        feedback = battle._avatar.guiSessionProvider.shared.feedback
        feedback.setVehicleState.assert_called_once_with(
            vehicle_id, runtime.feedback_event_id.VEHICLE_DEAD, False)
        battle._binding.arena_vehicle_killed.assert_called_once_with(
            vehicle_id, 0, 0)
        factory.destroy_all()

    def test_native_death_removes_outline_before_stock_model_replacement(self):
        runtime, factory, unused_binding, vehicle_id, vehicle = \
            self._ready_native_factory()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        battle._remote_factory = factory
        record = {
            'engine_id': vehicle_id, 'local': False, 'ready': True,
            'presentation': True, 'native_remote': True,
            'state': {'team': 2, 'health': 500, 'alive': True}}
        battle._records = {'bot:11': record}
        original_model = vehicle.model
        battle._outlined_engine_id = vehicle_id
        battle._outlined_entity = vehicle
        battle._outlined_vehicle = vehicle
        battle._outlined_model = original_model
        order = []

        def remove_edge(entity):
            order.append(('edge', entity.model))
            runtime.bigworld.edge_removes.append(entity)

        def replace_model(health, attacker_id, reason_id):
            order.append(('health', vehicle.model))
            vehicle.model = _Model()
            vehicle.appearance.compoundModel = vehicle.model
            vehicle.health_change = (health, attacker_id, reason_id)

        runtime.bigworld.wgDelEdgeDetectEntity = remove_edge
        vehicle.onHealthChanged = replace_model

        battle._apply_health(record, {'health': 0, 'alive': False})

        self.assertEqual(['edge', 'health'], [item[0] for item in order])
        self.assertIs(original_model, order[0][1])
        self.assertIs(original_model, order[1][1])
        self.assertEqual([vehicle], runtime.bigworld.edge_removes)
        self.assertIsNone(battle._outlined_engine_id)
        self.assertFalse(battle._outline_blocked)
        factory.destroy_all()

    def test_native_remove_paths_clear_outline_before_entity_destruction(self):
        for path in ('event', 'tombstone'):
            with self.subTest(path=path):
                runtime, factory, unused_binding, vehicle_id, vehicle = \
                    self._ready_native_factory()
                battle = BattleRuntime(runtime)
                battle.client = _Client()
                battle._avatar = runtime.bigworld.avatar
                battle._binding = mock.Mock()
                battle._remote_factory = factory
                record = {
                    'engine_id': vehicle_id, 'local': False, 'ready': True,
                    'presentation': True, 'native_remote': True,
                    'arena_added': True,
                    'state': {'team': 2, 'health': 500, 'alive': True}}
                battle._records = {'bot:11': record}
                battle._outlined_engine_id = vehicle_id
                battle._outlined_entity = vehicle
                battle._outlined_vehicle = vehicle
                battle._outlined_model = vehicle.model
                order = []
                runtime.bigworld.wgDelEdgeDetectEntity = (
                    lambda entity: order.append(('edge', entity)))
                original_destroy = factory.destroy

                def destroy(entity_id):
                    order.append(('destroy', entity_id))
                    self.assertIsNone(battle._outlined_engine_id)
                    return original_destroy(entity_id)

                factory.destroy = destroy
                if path == 'event':
                    battle._destroy_entity({'entity': 'bot:11'})
                else:
                    battle._flush_tombstone(record)

                self.assertEqual(
                    [('edge', vehicle), ('destroy', vehicle_id)], order)
                self.assertIsNone(runtime.bigworld.entity(vehicle_id))
                factory.destroy_all()

    def test_a_burning_bot_plays_and_stops_the_stock_1513_fire_extra(self):
        runtime = _runtime()
        log = []
        fire = _FireExtra(log)
        descriptor = _Descriptor()
        descriptor.extrasDict = {'fire': fire}
        descriptor.extras = {22: fire}
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        vehicle_id = factory.create(descriptor, {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True,
            'gunAnglesPacked': 0}, _Vector(), (0.0, 0.0, 0.0))
        vehicle = factory.get(vehicle_id)
        compound = vehicle.model
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._remote_factory = factory
        record = {'engine_id': vehicle_id, 'local': False,
                  'presentation': True}
        ignited = ({'kind': 'fire', 'state': True, 'cause': 'shot'},)
        extinguished = ({'kind': 'fire', 'state': False, 'cause': 'repair'},)

        with mock.patch.dict(sys.modules, _bound_effects_modules(log)):
            vehicle.is_on_fire = True
            self.assertFalse(battle._present_critical(record, ignited, 0))
            self.assertTrue(fire.isRunningFor(vehicle))
            # A repeated burning state must not start a second flame.
            battle._present_critical(record, ignited, 0)
            vehicle.is_on_fire = False
            battle._present_critical(record, extinguished, 0)

        self.assertFalse(fire.isRunningFor(vehicle))
        # A vehicle that survives keys the flame off; the appearance owns
        # the player until the compound goes away.
        factory.destroy_all()
        self.assertEqual([
            ('effect play', compound), ('fire start', compound),
            ('fire cleanup', compound), ('effect key off', compound),
            ('effect stop', compound)], log)

    def test_a_fire_received_while_hidden_starts_when_the_bot_is_respotted(self):
        runtime = _runtime()
        log = []
        fire = _FireExtra(log)
        descriptor = _Descriptor()
        descriptor.extrasDict = {'fire': fire}
        descriptor.extras = {22: fire}
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        vehicle_id = factory.create(descriptor, {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True,
            'gunAnglesPacked': 0}, _Vector(), (0.0, 0.0, 0.0))
        vehicle = factory.get(vehicle_id)
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        battle._remote_factory = factory
        record = {
            'engine_id': vehicle_id, 'local': False, 'ready': True,
            'presentation': True, 'native_remote': False,
            'visual_started': False, 'spot_visible': False,
            'spot_marker_visible': False,
            'state': {'team': 2, 'health': 500, 'alive': True}}
        battle._records = {'bot:11': record}
        ignited = ({'kind': 'fire', 'state': True, 'cause': 'shot'},)

        with mock.patch.dict(sys.modules, _bound_effects_modules(log)):
            vehicle.is_on_fire = True
            self.assertFalse(battle._present_critical(record, ignited, 0))
            self.assertFalse(fire.isRunningFor(vehicle))
            self.assertTrue(battle._set_record_spot_visibility(
                record, True, True))
            self.assertTrue(fire.isRunningFor(vehicle))

        factory.destroy_all()

    def test_a_burning_local_player_drives_the_same_fire_extra(self):
        runtime = _runtime()
        log = []
        fire = _FireExtra(log)
        descriptor = _Descriptor()
        descriptor.extrasDict = {'fire': fire}
        descriptor.extras = {22: fire}
        entity = _Vehicle(10, descriptor, _Vector(), (0, 0, 0),
                          {'health': 500})
        entity.extras = {}
        entity.appearance.isUnderwater = False
        entity.appearance.switchFireVibrations = lambda start: None
        entity.appearance.boundEffects = _bound_effects_modules(
            log)['helpers.bound_effects'].ModelBoundEffects(entity.model)
        runtime.bigworld.entities[10] = entity
        runtime.constants.DAMAGE_INFO_INDICES[
            'DEVICE_STARTED_FIRE_AT_SHOT'] = 14
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        record = {'engine_id': 10, 'local': True}

        entity.is_on_fire = True
        battle._present_critical(
            record, ({'kind': 'fire', 'state': True, 'cause': 'shot'},), 0)
        self.assertTrue(fire.isRunningFor(entity))
        entity.is_on_fire = False
        battle._present_critical(
            record, ({'kind': 'fire', 'state': False, 'cause': 'repair'},), 0)

        self.assertFalse(fire.isRunningFor(entity))
        self.assertEqual([
            ('effect play', entity.model), ('fire start', entity.model),
            ('fire cleanup', entity.model), ('effect key off', entity.model)],
            log)

    def test_a_still_burning_bot_drains_its_extra_when_the_visual_goes(self):
        runtime = _runtime()
        log = []
        fire = _FireExtra(log)
        descriptor = _Descriptor()
        descriptor.extrasDict = {'fire': fire}
        descriptor.extras = {22: fire}
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        vehicle_id = factory.create(descriptor, {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True,
            'gunAnglesPacked': 0}, _Vector(), (0.0, 0.0, 0.0))
        vehicle = factory.get(vehicle_id)
        compound = vehicle.model

        with mock.patch.dict(sys.modules, _bound_effects_modules(log)):
            fire.startFor(vehicle)

        factory.destroy_all()

        self.assertEqual({}, vehicle.extras)
        self.assertEqual([
            ('effect play', compound), ('fire start', compound),
            ('fire cleanup', compound), ('effect key off', compound),
            ('effect stop', compound)], log)

    def test_a_burning_bot_stops_its_flame_on_the_retained_wreck(self):
        """A retained cover model must own no surviving live-tank effect."""
        runtime = _runtime()
        log = []
        fire = _FireExtra(log)
        descriptor = _Descriptor()
        descriptor.extrasDict = {'fire': fire}
        descriptor.extras = {22: fire}
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        vehicle_id = factory.create(descriptor, {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True,
            'gunAnglesPacked': 0}, _Vector(), (0.0, 0.0, 0.0))
        vehicle = factory.get(vehicle_id)
        undamaged = vehicle.model
        loads = []
        runtime.bigworld.loadResourceListBG = (
            lambda assemblers, callback: loads.append((assemblers, callback)))
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        battle._remote_factory = factory
        record = {'engine_id': vehicle_id, 'local': False, 'ready': True,
                  'presentation': True,
                  'state': {'team': 2, 'health': 500}}
        battle._records = {'bot:11': record}

        with mock.patch.dict(sys.modules, _bound_effects_modules(log)):
            vehicle.is_on_fire = True
            battle._present_critical(
                record, ({'kind': 'fire', 'state': True},), 0)
            self.assertTrue(fire.isRunningFor(vehicle))

            battle._apply_health(record, {'health': 0, 'alive': False})

            self.assertFalse(fire.isRunningFor(vehicle))
            self.assertEqual([], loads)

        self.assertIs(undamaged, vehicle.model)
        self.assertIs(vehicle.model, vehicle.bw_entity.model)
        self.assertEqual({}, vehicle.extras)
        self.assertIsNone(vehicle.appearance._bound_effects)
        # Every flame call names the retained compound and the final stop
        # leaves it with no EffectsListPlayer ownership.
        self.assertEqual([
            ('effect play', undamaged), ('fire start', undamaged),
            ('fire cleanup', undamaged), ('effect key off', undamaged),
            ('effect stop', undamaged)], log)
        factory.destroy_all()

    def test_remote_visual_cleanup_survives_destroy_entity_failure(self):
        runtime = _runtime()
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        vehicle_id = factory.create(_Descriptor(), {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True,
            'gunAnglesPacked': 0}, _Vector(10.0, 2.0, 30.0),
            (0.25, -0.1, 0.5))
        vehicle = factory.get(vehicle_id)
        visual = runtime.bigworld.entity(vehicle.bw_entity_id)
        visual_id = vehicle.bw_entity_id
        model = vehicle.model
        destroy_entity = runtime.bigworld.destroyEntity
        runtime.bigworld.destroyEntity = mock.Mock(
            side_effect=RuntimeError('destroy failed'))

        with self.assertRaisesRegex(RuntimeError, 'destroy failed'):
            factory.destroy(vehicle_id)

        self.assertIsNone(visual.model)
        self.assertIsNot(model.matrix, vehicle.matrix)
        self.assertIsNone(vehicle.model)
        self.assertIsNone(vehicle.bw_entity)
        self.assertFalse(vehicle.inWorld)
        self.assertIs(vehicle, factory.get(vehicle_id))
        self.assertEqual(visual_id, vehicle.bw_entity_id)

        runtime.bigworld.destroyEntity = destroy_entity
        self.assertTrue(factory.destroy(vehicle_id))
        self.assertIsNone(factory.get(vehicle_id))
        factory.destroy_all()

    def test_destroy_before_resource_callback_prevents_late_visual(self):
        runtime = _runtime()
        callbacks = []
        runtime.bigworld.loadResourceListBG = (
            lambda assemblers, callback: callbacks.append(
                (assemblers, callback)))
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        descriptor = _Descriptor()
        vehicle_id = factory.create(descriptor, {
            'publicInfo': {'team': 1}, 'health': 500,
            'isCrewActive': True, 'gunAnglesPacked': 0},
            _Vector(), (0.0, 0.0, 0.0))

        self.assertTrue(factory.destroy(vehicle_id))
        callbacks[0][1]({descriptor.name: _Model()})

        self.assertIsNone(factory.get(vehicle_id))
        self.assertFalse(any(
            getattr(entity, 'model', None) is not None
            for entity in runtime.bigworld.entities.values()))
        factory.destroy_all()

    def test_factory_releases_each_unique_hit_tester_once(self):
        runtime = _runtime()
        tester = types.SimpleNamespace(
            bbox=((0.0, 0.0, 0.0), (1.0, 1.0, 1.0), None),
            loadBspModel=mock.Mock(), releaseBspModel=mock.Mock())
        descriptor = _Descriptor()
        descriptor.getHitTesters = lambda: (tester, tester)
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        properties = {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True, 'gunAnglesPacked': 0}

        factory.create(
            descriptor, properties, _Vector(), (0.0, 0.0, 0.0))
        factory.create(
            descriptor, properties, _Vector(), (0.0, 0.0, 0.0))
        factory.destroy_all()

        tester.loadBspModel.assert_called_once_with()
        tester.releaseBspModel.assert_called_once_with()

    def test_prepare_descriptor_owns_bbox_lifecycle_before_shape_read(self):
        runtime = _runtime()
        descriptor = _Descriptor('ussr:R11_MS-1', loaded=False)
        testers = descriptor.getHitTesters()
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)

        self.assertTrue(all(tester.bbox is None for tester in testers))

        self.assertIs(descriptor, factory.prepare_descriptor(descriptor))
        self.assertEqual(
            (1.5, 3.5, -0.8, 2.0),
            tank_collision.chassis_shape(descriptor))
        factory.prepare_descriptor(descriptor)

        self.assertEqual([1, 1, 1, 1], [
            tester.load_calls for tester in testers])
        factory.destroy_all()
        self.assertTrue(all(
            not hasattr(tester, 'bbox') for tester in testers))
        self.assertEqual([1, 1, 1, 1], [
            tester.release_calls for tester in testers])
        self.assertNotIn(id(descriptor), tank_collision._SHAPE_CACHE)

    def test_factory_cleanup_forgets_only_owned_descriptor_shapes(self):
        runtime = _runtime()
        first = _Descriptor('ussr:R11_MS-1', loaded=False)
        second = _Descriptor('ussr:R04_T-34', loaded=False)
        self.addCleanup(tank_collision._SHAPE_CACHE.pop, id(second), None)
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)

        factory.prepare_descriptor(first)
        tank_collision.chassis_shape(first)
        tank_collision._SHAPE_CACHE[id(second)] = (
            second, (1.0, 2.0, -0.5, 1.5))

        factory.destroy_all()

        self.assertNotIn(id(first), tank_collision._SHAPE_CACHE)
        self.assertIs(
            second, tank_collision._SHAPE_CACHE[id(second)][0])

    def test_factory_and_stock_share_idempotent_hit_tester_lifecycle(self):
        runtime = _runtime()
        tester = _HitTester1513(
            _Vector(-1.0, -1.0, -1.0),
            _Vector(1.0, 1.0, 1.0), loaded=False)
        planning = _Descriptor('ussr:R11_MS-1', loaded=False)
        native = _Descriptor('ussr:R11_MS-1', loaded=False)
        planning.getHitTesters = lambda: (tester,)
        native.getHitTesters = lambda: (tester,)
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)

        factory.prepare_descriptor(planning)
        for shared in native.getHitTesters():
            shared.loadBspModel()
        factory.destroy_all()
        for shared in native.getHitTesters():
            shared.releaseBspModel()

        self.assertFalse(hasattr(tester, 'bbox'))
        self.assertEqual(2, tester.load_calls)
        self.assertEqual(2, tester.release_calls)

    def test_prepare_descriptor_rejects_tester_without_loaded_bbox(self):
        runtime = _runtime()
        tester = types.SimpleNamespace(
            bbox=None, loadBspModel=mock.Mock(),
            releaseBspModel=mock.Mock())
        descriptor = _Descriptor()
        descriptor.getHitTesters = lambda: (tester,)
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)

        with self.assertRaisesRegex(RuntimeError, 'bbox did not load'):
            factory.prepare_descriptor(descriptor)

        tester.loadBspModel.assert_called_once_with()
        tester.releaseBspModel.assert_called_once_with()
        self.assertEqual({}, factory._hit_testers)
        factory.destroy_all()

    def test_destroy_all_restores_every_owner_after_one_destroy_fails(self):
        runtime = _runtime()
        original_entity = runtime.bigworld.entity
        original_entities = runtime.bigworld.entities
        tester = types.SimpleNamespace(
            bbox=((0.0, 0.0, 0.0), (1.0, 1.0, 1.0), None),
            loadBspModel=mock.Mock(), releaseBspModel=mock.Mock())
        descriptor = _Descriptor()
        descriptor.getHitTesters = lambda: (tester,)
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        properties = {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True, 'gunAnglesPacked': 0}
        first = factory.create(
            descriptor, properties, _Vector(), (0.0, 0.0, 0.0))
        second = factory.create(
            descriptor, properties, _Vector(), (0.0, 0.0, 0.0))
        visual_ids = [factory.get(first).bw_entity_id,
                      factory.get(second).bw_entity_id]
        destroy = runtime.bigworld.destroyEntity
        attempted = []

        def fail_first(entity_id):
            attempted.append(entity_id)
            if len(attempted) == 1:
                raise RuntimeError('first visual failed')
            destroy(entity_id)

        runtime.bigworld.destroyEntity = fail_first
        with self.assertRaisesRegex(RuntimeError, 'first visual failed'):
            factory.destroy_all()

        self.assertEqual(visual_ids, attempted)
        self.assertEqual([first], list(factory._vehicles))
        tester.releaseBspModel.assert_not_called()
        self.assertIsNot(runtime.bigworld.entity, original_entity)
        self.assertIsNot(runtime.bigworld.entities, original_entities)

        factory.destroy_all()

        self.assertEqual(
            [visual_ids[0], visual_ids[1], visual_ids[0]], attempted)
        self.assertEqual({}, factory._vehicles)
        tester.releaseBspModel.assert_called_once_with()
        self.assertEqual(runtime.bigworld.entity, original_entity)
        self.assertIs(runtime.bigworld.entities, original_entities)

    def test_failed_post_create_attach_destroys_orphan_visual(self):
        runtime = _runtime()
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        descriptor = _Descriptor()

        with mock.patch.object(
                RemoteVehicle, 'attach_visual',
                side_effect=RuntimeError('attach failed')):
            vehicle_id = factory.create(descriptor, {
                'publicInfo': {'team': 1}, 'health': 500,
                'isCrewActive': True, 'gunAnglesPacked': 0},
                _Vector(), (0.0, 0.0, 0.0))

        self.assertIn('attach failed', str(factory.error(vehicle_id)))
        self.assertFalse(any(
            getattr(entity, 'model', None) is not None
            for entity in runtime.bigworld.entities.values()))
        factory.destroy_all()


class BattleRuntimeContractTests(unittest.TestCase):
    def setUp(self):
        self._current_vehicle_patch = mock.patch.dict(
            sys.modules,
            {'CurrentVehicle': _mounted_current_vehicle_module()})
        self._current_vehicle_patch.start()
        self.addCleanup(self._current_vehicle_patch.stop)

    def test_local_state_falls_back_before_roster_publishes_the_player(self):
        battle = BattleRuntime(_runtime())
        battle.client = _Client()
        battle._start_message = {
            'players': [{
                'id': 2, 'team': 2, 'slot': 0, 'name': 'Remote',
                'vehicle': 'ussr:R04_T-34', 'health': 450,
            }],
        }

        self.assertEqual({
            'id': 1, 'name': 'Player', 'vehicle': 'ussr:R11_MS-1',
            'team': 1, 'slot': 0, 'health': 500, 'max_health': 500,
            'alive': True,
        }, battle._local_state())

    def test_frame_diagnostics_attributes_work_to_the_next_interval(self):
        wall = [0.0]
        payloads = []

        def writer(payload):
            payloads.append(payload)
            wall[0] += 0.003

        diagnostics = _FrameDiagnostics(
            clock=lambda: wall[0], writer=writer, window_seconds=0.25)
        first = diagnostics.begin(0.0, 0.02)
        wall[0] = 0.006
        diagnostics.finish(
            first, 0.0, 0.02, 0.02, {'local': 0.004},
            {'lane': 7}, {'role': 'authority', 'speed': 14.0},
            probe_durations={'lane': 0.005}, projectile={
                'active': 29, 'chords': 58, 'debt': 0.05,
                'advance': 0.004, 'terminals': 1, 'scans': 1740,
                'candidates': 3})
        second = diagnostics.begin(0.12, 0.12)
        wall[0] = 0.125
        diagnostics.finish(
            second, 0.12, 0.10, 0.10, {'bots_update': 0.002},
            {'visibility': 3}, {
                'role': 'authority', 'probe_timing': 'active'})
        third = diagnostics.begin(0.30, 0.18)
        wall[0] = 0.31
        diagnostics.finish(
            third, 0.30, 0.10, 0.10, {'spot': 0.001}, {},
            {'role': 'authority'})

        self.assertEqual(1, len(payloads))
        first_row = next(
            line for line in payloads[0].splitlines()
            if 'cause=1 next=2' in line)
        self.assertIn('gap_ms=120.000', first_row)
        self.assertIn('raw_dt_ms=120.000', first_row)
        self.assertIn('local:4.000', first_row)
        self.assertIn('lane:7', first_row)
        self.assertIn('probe_ms=', first_row)
        self.assertIn('lane:5.000', first_row)
        self.assertIn('active:29,chords:58,debt_ms:50.000', first_row)
        self.assertIn('summary v=2', payloads[0])
        self.assertIn('role=authority probe_timing=active', payloads[0])
        self.assertIn('probe_ms_avg_max', payloads[0])
        self.assertIn('projectile_avg_max', payloads[0])
        self.assertIn('chords=29.00/58', payloads[0])
        self.assertIn('scans=870.00/1740', payloads[0])
        self.assertTrue(diagnostics._pending['emitted'])
        self.assertAlmostEqual(
            0.003, diagnostics._pending['stages']['diag_emit'])

    def test_frame_diagnostics_disable_themselves_when_logging_fails(self):
        wall = [0.0]

        def reject_log(unused_payload):
            raise IOError('python.log is unavailable')

        diagnostics = _FrameDiagnostics(
            clock=lambda: wall[0], writer=reject_log, window_seconds=0.25)
        first = diagnostics.begin(0.0, 0.02)
        wall[0] = 0.001
        diagnostics.finish(first, 0.0, 0.02, 0.02, {}, {}, {})
        diagnostics.begin(0.30, 0.30)
        wall[0] = 0.31

        diagnostics.finish(2, 0.30, 0.10, 0.10, {}, {}, {})

        self.assertFalse(diagnostics.enabled)
        self.assertIsNone(diagnostics._pending)

    def test_frame_diagnostics_use_a_short_first_window_then_slow_down(self):
        production = BattleRuntime(_runtime())._frame_diagnostics
        self.assertEqual(5.0, production._window_seconds)
        self.assertEqual(30.0, production._steady_window_seconds)

        payloads = []
        diagnostics = _FrameDiagnostics(
            clock=lambda: 0.0, writer=payloads.append,
            window_seconds=1.0, initial_window_seconds=0.25)
        diagnostics._samples = 1
        diagnostics._window_elapsed = 0.25
        diagnostics._emit_due = True

        diagnostics.finish(1, 0.0, 0.0, 0.0, {}, {}, {})

        self.assertEqual(1, len(payloads))
        self.assertEqual(1.0, diagnostics._window_seconds)
        diagnostics.reset()
        self.assertEqual(0.25, diagnostics._window_seconds)

    def test_network_deadlines_remove_main_thread_delay_from_periods(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._battle_live = False
        battle._config = {
            'prebattleCountdownSeconds': 15.0,
            'battleDurationSeconds': 900.0}
        battle.client = _Client()
        battle.client.combat_deadline = 110.0
        battle.client.combat_end_deadline = 1010.0
        battle.client.combat_duration = 900.0
        battle._binding = types.SimpleNamespace(arena_period=mock.Mock())
        battle._clock = mock.Mock(return_value=50.0)

        module = sys.modules[BattleRuntime.__module__]
        with mock.patch.object(
                module, '_monotonic_time', return_value=100.0):
            self.assertTrue(battle.on_battle_live({
                'countdown_seconds': 15.0,
                'battle_duration_seconds': 900.0}))

        battle._binding.arena_period.assert_called_once_with(
            'prebattle', 10.0)
        self.assertEqual(60.0, battle._prebattle_deadline)
        self.assertFalse(battle.on_battle_live({
            'countdown_seconds': 15.0,
            'battle_duration_seconds': 900.0}))
        battle._binding.arena_period.assert_called_once_with(
            'prebattle', 10.0)

        battle._next_spotting_time = 50.1
        with mock.patch.object(
                module, '_monotonic_time', return_value=110.0):
            self.assertTrue(battle._begin_battle())
        self.assertEqual(
            mock.call('battle', 900.0),
            battle._binding.arena_period.call_args_list[-1])
        self.assertEqual(0.0, battle._next_spotting_time)

    def test_native_shot_ray_is_copied_before_normalise_or_scatter(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        native_start = _ReadOnlyVector(1.0, 2.0, 3.0)
        native_direction = _ReadOnlyVector(0.0, 0.0, 4.0)
        battle._avatar.gunRotator.getCurShotPosition = lambda: (
            native_start, native_direction)

        start, direction = battle._mutable_shot_ray()
        direction.x = 0.25

        self.assertEqual((1.0, 2.0, 3.0),
                         (start.x, start.y, start.z))
        self.assertEqual((0.0, 0.0, 4.0),
                         (native_direction.x, native_direction.y,
                          native_direction.z))

    def test_local_tank_contact_uses_copied_separation_and_impulse(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        local = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                         {'health': 500})
        remote = _Vehicle(11, _Descriptor(), _Vector(0.0, 0.0, 6.5),
                          (0, 0, 0), {'health': 500})
        runtime.bigworld.entities[11] = remote
        battle._records = {'player:2': {
            'engine_id': 11, 'network_id': 2, 'kind': 'player',
            'local': False, 'ready': True, 'tombstone': False,
            'state': {'x': 0.0, 'y': 0.0, 'z': 6.5, 'yaw': 0.0,
                      'speed': 0.0, 'alive': True,
                      'effective_params': _effective_params_snapshot()}}}
        battle._local_physics = {'mass': 25000.0}
        battle._local_speed = 5.0
        battle._motion_is_clear = mock.Mock(return_value=True)
        battle._baked_pose_safe = mock.Mock(return_value=True)

        position = battle._resolve_local_tank_contacts(
            local, (0.0, 0.0, 0.0), 0.0, 0.1)

        self.assertLess(position[2], 0.0)
        self.assertLess(battle._local_speed, 5.0)
        battle._motion_is_clear.assert_called_once()
        self.assertNotIn(
            'allow_crush_drive',
            battle._motion_is_clear.call_args.kwargs)
        self.assertEqual(
            0.0, battle._motion_is_clear.call_args.kwargs['hull_yaw'])
        battle._baked_pose_safe.assert_called_once()

    def test_tank_contact_can_push_a_stale_outside_pose_back_in(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._arena_bounds = (-300.0, -300.0, 300.0, 300.0)
        battle._local_physics = {'mass': 25000.0}
        battle._contact_tanks = mock.Mock(return_value=[])
        battle._poll_local_ram_contact_episodes = mock.Mock()
        battle._baked_pose_safe = mock.Mock(return_value=False)
        entity = _Vehicle(
            10, _Descriptor(), _Vector(), (0, 0, 0), {'health': 500})
        contact = {
            'cooldowns': {}, 'contacts': frozenset(),
            'delta_velocity': (0.0, 0.0), 'correction': (-0.2, 0.0),
        }

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'tank_collision.resolve_tank', return_value=contact), \
                mock.patch(
                    'gui.mods.offline_lan_0922.battle_runtime.'
                    'world_collision.check_horizontal_collision',
                    return_value='clear'):
            position = battle._resolve_local_tank_contacts(
                entity, (301.0, 0.0, 0.0), 0.0, 0.1)

        self.assertAlmostEqual(300.8, position[0])
        self.assertFalse(battle._baked_pose_safe(position))

    def test_local_bot_ram_receipt_preserves_pre_separation_pose(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        local = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                         {'health': 500})
        remote = _Vehicle(11, _Descriptor(), _Vector(0.0, 0.0, 6.5),
                          (0, 0, 0), {'health': 500})
        runtime.bigworld.entities[11] = remote
        battle._records = {'bot:11': {
            'engine_id': 11, 'network_id': 11, 'kind': 'bot',
            'local': False, 'ready': True, 'tombstone': False,
            'presented_pose': {
                'x': 0.0, 'y': 0.0, 'z': 6.5, 'yaw': math.pi},
            'presentation_time_us': 123000,
            # The newest canonical sample is already well ahead of the hull
            # being drawn. Contact must use the actual presented pose.
            'state': {'id': 11, 'x': 0.0, 'y': 0.0, 'z': 30.0,
                      'yaw': math.pi, 'speed': 0.0, 'alive': True,
                      'team': 2}}}
        battle._bots = types.SimpleNamespace(states={11: {
            'id': 11, 'mass': 25000.0,
            'collision_shape': (1.5, 3.5, 0.0, 1.0),
            'vehicle': 'ussr:T-34', 'team': 2,
        }})
        for revision, sample_time, z in (
                (36, 100000, 6.5), (37, 200000, 6.5),
                (38, 300000, 6.5)):
            self.assertTrue(battle._remember_ram_bot_snapshot({
                'bot_state_revision': revision,
                'bot_state_time_us': sample_time,
                'bots': [{'id': 11, 'x': 0.0, 'y': 0.0, 'z': z,
                          'yaw': math.pi, 'alive': True}],
            }))
        battle._last_snapshot = {'bot_state_revision': 38}
        battle._local_physics = {'mass': 25000.0}
        battle._local_speed = 10.0
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._local_position = (0.0, 0.0, 0.0)
        battle._local_yaw = 0.0
        battle._local_pitch = 0.21
        battle._local_roll = -0.13
        battle._local_matrix = local.matrix
        battle._estimated_motion_time_us = mock.Mock(return_value=123000)
        local.filter.velocity = _Vector(0.0, 0.0, 10.0)
        remote.filter.velocity = _Vector(0.0, 0.0, 0.0)
        battle._native_ram_vehicle_armor = mock.Mock(side_effect=[
            {'armor': 20.0, 'screened': False},
            {'armor': 30.0, 'screened': True},
        ])
        battle._motion_is_clear = mock.Mock(return_value=True)
        battle._baked_pose_safe = mock.Mock(return_value=True)

        self.assertTrue(battle._observe_native_ram_contact(
            local, remote, _Vector(0.0, 0.0, 3.25), 10.0))
        corrected = battle._resolve_local_tank_contacts(
            local, (0.0, 0.0, 0.0), 0.0, 0.1)
        receipt = battle.local_ram_contact()

        self.assertLess(corrected[2], 0.0)
        self.assertEqual((11, 37), (
            receipt['bot_id'], receipt['bot_state_revision']))
        self.assertEqual(123000, receipt['presentation_time_us'])
        self.assertEqual((20.0, 30.0), (
            receipt['contact_armor_player'],
            receipt['contact_armor_bot']))
        self.assertEqual((0.0, -1.0), (
            receipt['contact_normal_x'], receipt['contact_normal_z']))
        self.assertTrue(receipt['contact_screened_bot'])
        self.assertEqual((0.0, 0.0, 0.0), (
            receipt['x'], receipt['y'], receipt['z']))
        self.assertEqual((0.21, -0.13), (
            receipt['pitch'], receipt['roll']))
        self.assertGreater(receipt['vz'], 0.0)
        self.assertLess(battle._local_speed, 10.0)
        player_inward = battle._native_ram_vehicle_armor.call_args_list[
            0].args[3]
        bot_inward = battle._native_ram_vehicle_armor.call_args_list[
            1].args[3]
        self.assertEqual((0.0, -1.0), player_inward)
        self.assertEqual((-0.0, 1.0), bot_inward)

    def test_local_bot_ram_polling_emits_one_receipt_per_overlap_episode(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        local = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                         {'health': 500})
        remote = _Vehicle(11, _Descriptor(), _Vector(0.0, 0.0, 6.5),
                          (0, 0, 0), {'health': 500})
        runtime.bigworld.entities[11] = remote
        record = {
            'engine_id': 11, 'network_id': 11, 'kind': 'bot',
            'local': False, 'ready': True, 'tombstone': False,
            'presented_pose': {
                'x': 0.0, 'y': 0.0, 'z': 6.5, 'yaw': math.pi},
            'presentation_time_us': 200000,
            'state': {'id': 11, 'x': 0.0, 'y': 0.0, 'z': 6.5,
                      'yaw': math.pi, 'speed': 0.0, 'alive': True,
                      'team': 2}}
        battle._records = {'bot:11': record}
        battle._bots = types.SimpleNamespace(states={11: {
            'id': 11, 'mass': 25000.0, 'speed': 0.0,
            'collision_shape': (1.5, 3.5, 0.0, 1.0),
            'vehicle': 'ussr:T-34', 'team': 2,
        }})
        for revision, sample_time in (
                (36, 100000), (37, 200000), (38, 300000)):
            self.assertTrue(battle._remember_ram_bot_snapshot({
                'bot_state_revision': revision,
                'bot_state_time_us': sample_time,
                'bots': [{'id': 11, 'x': 0.0, 'y': 0.0, 'z': 6.5,
                          'yaw': math.pi, 'alive': True}],
            }))
        battle._last_snapshot = {'bot_state_revision': 38}
        battle._local_physics = {'mass': 25000.0}
        battle._local_speed = 10.0
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._local_position = (99.0, 0.0, 99.0)
        battle._local_yaw = 1.0
        battle._estimated_motion_time_us = mock.Mock(return_value=200000)
        battle._native_ram_vehicle_armor = mock.Mock(side_effect=[
            {'armor': 21.0, 'screened': False},
            {'armor': 31.0, 'screened': False},
            {'armor': 22.0, 'screened': False},
            {'armor': 32.0, 'screened': False},
        ])
        battle._motion_is_clear = mock.Mock(return_value=True)
        battle._baked_pose_safe = mock.Mock(return_value=True)

        battle._resolve_local_tank_contacts(
            local, (0.0, 0.0, 0.0), 0.0, 0.1)
        first = battle.local_ram_contact()
        battle._local_speed = 10.0
        battle._resolve_local_tank_contacts(
            local, (0.0, 0.0, 0.0), 0.0, 0.1)

        self.assertEqual(1, first['seq'])
        self.assertEqual((0.0, 0.0, 0.0),
                         (first['x'], first['y'], first['z']))
        self.assertEqual((21.0, 31.0), (
            first['contact_armor_player'], first['contact_armor_bot']))
        self.assertEqual(1, battle.local_ram_contact()['seq'])
        self.assertEqual(2, battle._native_ram_vehicle_armor.call_count)

        # One clear interpolated frame while the hulls are still approaching
        # is not physical separation and must not re-arm the same impact.
        record['presented_pose']['z'] = 20.0
        battle._resolve_local_tank_contacts(
            local, (0.0, 0.0, 0.0), 0.0, 0.1)
        record['presented_pose']['z'] = 6.5
        battle._local_speed = 10.0
        battle._resolve_local_tank_contacts(
            local, (0.0, 0.0, 0.0), 0.0, 0.1)

        self.assertEqual(1, battle.local_ram_contact()['seq'])

        # A clear frame whose relative motion is separating ends the episode.
        record['presented_pose']['z'] = 20.0
        local.filter.velocity = _Vector(0.0, 0.0, -10.0)
        battle._local_speed = -10.0
        battle._resolve_local_tank_contacts(
            local, (0.0, 0.0, 0.0), 0.0, 0.1)
        record['presented_pose']['z'] = 6.5
        local.filter.velocity = _Vector(0.0, 0.0, 10.0)
        battle._local_speed = 10.0
        battle._resolve_local_tank_contacts(
            local, (0.0, 0.0, 0.0), 0.0, 0.1)

        second = battle.local_ram_contact()
        self.assertEqual(2, second['seq'])
        self.assertEqual((22.0, 32.0), (
            second['contact_armor_player'], second['contact_armor_bot']))

    def test_local_bot_ram_polling_ignores_vertical_only_drop(self):
        battle = BattleRuntime(_runtime())
        battle._local_ram_episode_contacts = frozenset()
        battle._estimated_motion_time_us = mock.Mock(return_value=200000)
        battle._queue_ram_contact_proof = mock.Mock(return_value=True)
        record = {'network_id': 11}
        vehicle = object()
        own = {
            'x': 0.0, 'y': 0.8, 'z': 0.0, 'yaw': 0.0,
            'vx': 0.0, 'vy': -12.0, 'vz': 0.0,
            'shape': (1.5, 3.5, 0.0, 1.0),
            'ram_profile': {
                'spall_coefficient': 1.0, 'ramming_bonus': 0.0},
        }
        other = {
            'network_id': 11, 'kind': 'bot', 'alive': True,
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'vx': 0.0, 'vy': 0.0, 'vz': 0.0,
            'shape': (1.5, 3.5, 0.0, 1.0),
            '_record': record, '_vehicle': vehicle,
        }

        self.assertFalse(battle._poll_local_ram_contact_episodes(
            object(), own, (other,)))
        battle._queue_ram_contact_proof.assert_not_called()

    def test_local_friendly_bot_contact_does_not_queue_hp_receipt(self):
        battle = BattleRuntime(_runtime())
        battle._local_ram_episode_contacts = frozenset()
        battle._queue_ram_contact_proof = mock.Mock(return_value=True)
        own = {
            'team': 1,
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'vx': 0.0, 'vy': 0.0, 'vz': 10.0,
            'shape': (1.5, 3.5, 0.0, 1.0),
            'ram_profile': {
                'spall_coefficient': 1.0, 'ramming_bonus': 0.0},
        }
        teammate = {
            'network_id': 11, 'kind': 'bot', 'alive': True, 'team': 1,
            'x': 0.0, 'y': 0.0, 'z': 0.5, 'yaw': 0.0,
            'vx': 0.0, 'vy': 0.0, 'vz': 0.0,
            'shape': (1.5, 3.5, 0.0, 1.0),
            '_record': {'network_id': 11}, '_vehicle': object(),
        }

        self.assertFalse(battle._poll_local_ram_contact_episodes(
            object(), own, (teammate,)))
        battle._queue_ram_contact_proof.assert_not_called()

    def test_local_bot_ram_polling_accepts_deep_horizontal_overlap(self):
        battle = BattleRuntime(_runtime())
        battle._local_ram_episode_contacts = frozenset()
        battle._estimated_motion_time_us = mock.Mock(return_value=200000)
        battle._queue_ram_contact_proof = mock.Mock(return_value=True)
        record = {'network_id': 11}
        vehicle = object()
        own = {
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'vx': 0.0, 'vy': 0.0, 'vz': 10.0,
            'shape': (1.5, 3.5, 0.0, 1.0),
            'ram_profile': {
                'spall_coefficient': 1.0, 'ramming_bonus': 0.0},
        }
        other = {
            'network_id': 11, 'kind': 'bot', 'alive': True,
            'x': 0.0, 'y': 0.0, 'z': 0.5, 'yaw': 0.0,
            'vx': 0.0, 'vy': 0.0, 'vz': 0.0,
            'shape': (1.5, 3.5, 0.0, 1.0),
            '_record': record, '_vehicle': vehicle,
        }

        self.assertTrue(battle._poll_local_ram_contact_episodes(
            object(), own, (other,)))

        call = battle._queue_ram_contact_proof.call_args
        self.assertEqual((0.0, 0.0, 10.0), call.args[4])
        self.assertAlmostEqual(0.5, call.args[3].y)
        self.assertEqual((-0.0, -1.0), call.kwargs['contact_normal'])

    def test_local_bot_ram_polling_freezes_slope_pose_at_world_midpoint(self):
        battle = BattleRuntime(_runtime())
        battle._local_ram_episode_contacts = frozenset()
        battle._local_pitch = 0.18
        battle._local_roll = -0.11
        battle._estimated_motion_time_us = mock.Mock(return_value=200000)
        battle._queue_ram_contact_proof = mock.Mock(return_value=True)
        record = {'network_id': 11}
        vehicle = object()
        own = {
            'x': 10.0, 'y': 5.0, 'z': 20.0, 'yaw': 0.0,
            'vx': 0.0, 'vy': 0.0, 'vz': 10.0,
            'shape': (1.5, 3.5, 0.0, 1.0),
            'ram_profile': {
                'spall_coefficient': 1.0, 'ramming_bonus': 0.0},
        }
        other = {
            'network_id': 11, 'kind': 'bot', 'alive': True,
            'x': 10.0, 'y': 5.2, 'z': 26.5, 'yaw': math.pi,
            'pitch': -0.04, 'roll': 0.07,
            'vx': 0.0, 'vy': 0.0, 'vz': 0.0,
            'shape': (1.5, 3.5, 0.0, 1.0),
            '_record': record, '_vehicle': vehicle,
        }

        self.assertTrue(battle._poll_local_ram_contact_episodes(
            object(), own, (other,)))

        call = battle._queue_ram_contact_proof.call_args
        self.assertEqual((10.0, 5.6, 23.25), tuple(call.args[3]))
        self.assertEqual(
            (10.0, 5.0, 20.0, 0.0, 0.18, -0.11),
            call.kwargs['own_pose'])
        self.assertEqual(
            (10.0, 5.2, 26.5, math.pi, -0.04, 0.07),
            call.kwargs['bot_pose'])
        self.assertEqual((0.0, -1.0), call.kwargs['contact_normal'])

    def test_local_ram_ledger_stops_resending_at_admission_and_retires_at_resolution(self):
        battle = BattleRuntime(_runtime())
        battle.client = _Client()
        battle._local_ram_receipts[1] = {'seq': 1, 'bot_id': 11}
        battle._local_ram_receipts[2] = {'seq': 2, 'bot_id': 12}
        battle._local_ram_receipt = dict(battle._local_ram_receipts[2])

        self.assertTrue(battle._ack_local_ram_contacts({
            'players': [{
                'id': 1, 'ram_contact_admitted_seq': 1,
                'ram_contact_resolved_seq': 0,
            }],
        }))

        self.assertEqual([1, 2], list(battle._local_ram_receipts))
        self.assertEqual([2], [
            value['seq'] for value in battle.local_ram_contacts()])
        self.assertEqual(2, battle.local_ram_contact()['seq'])

        self.assertTrue(battle._ack_local_ram_contacts({
            'players': [{
                'id': 1, 'ram_contact_admitted_seq': 2,
                'ram_contact_resolved_seq': 1,
            }],
        }))
        self.assertEqual([2], list(battle._local_ram_receipts))
        self.assertEqual([], battle.local_ram_contacts())
        self.assertEqual(2, battle.local_ram_contact()['seq'])

        self.assertTrue(battle._ack_local_ram_contacts({
            'players': [{
                'id': 1, 'ram_contact_admitted_seq': 2,
                'ram_contact_resolved_seq': 2,
            }],
        }))
        self.assertEqual([], list(battle._local_ram_receipts))
        self.assertIsNone(battle.local_ram_contact())

    def test_native_ram_plate_uses_first_structural_material(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        descriptor = _Descriptor()
        structural = types.SimpleNamespace(
            armor=73.0, vehicleDamageFactor=1.0)
        descriptor.hull.hitTester = types.SimpleNamespace(
            bbox=descriptor.hull.hitTester.bbox,
            localHitTest=mock.Mock(return_value=[
                (1.0, None, 1.0, 7)]))
        descriptor.hull.materials = {7: structural}
        vehicle = _Vehicle(
            11, descriptor, _Vector(), (0.0, 0.0, 0.0),
            {'health': 500})

        armor = battle._native_ram_vehicle_armor(
            vehicle, vehicle.matrix, _Vector(0.0, 0.0, 3.5),
            (0.0, -1.0))

        self.assertEqual({'armor': 73.0, 'screened': False}, armor)

    def test_native_ram_plate_traces_along_contact_normal(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        descriptor = _Descriptor()
        structural = types.SimpleNamespace(
            armor=73.0, vehicleDamageFactor=1.0)
        hit_tester = types.SimpleNamespace(
            bbox=descriptor.hull.hitTester.bbox,
            localHitTest=mock.Mock(return_value=[
                (1.0, None, 1.0, 7)]))
        descriptor.hull.hitTester = hit_tester
        descriptor.hull.materials = {7: structural}
        vehicle = _Vehicle(
            11, descriptor, _Vector(), (0.0, 0.0, 0.0),
            {'health': 500})

        armor = battle._native_ram_vehicle_armor(
            vehicle, vehicle.matrix, _Vector(1.4, 0.0, 2.5),
            (-1.0, 0.0))

        self.assertEqual({'armor': 73.0, 'screened': False}, armor)
        start, end = hit_tester.localHitTest.call_args.args
        self.assertGreater(start.x, 1.4)
        self.assertAlmostEqual(0.0, end.x)
        self.assertEqual(start.z, end.z)
        self.assertAlmostEqual(2.5, end.z)

    def test_native_ram_plate_selects_material_on_impact_axis(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        descriptor = _Descriptor()
        front = types.SimpleNamespace(
            armor=100.0, vehicleDamageFactor=1.0)
        side = types.SimpleNamespace(
            armor=80.0, vehicleDamageFactor=1.0)

        def directional_hit(start, end):
            material = (8 if abs(end.x - start.x) >
                        abs(end.z - start.z) else 7)
            return [(1.0, None, 1.0, material)]

        descriptor.hull.hitTester = types.SimpleNamespace(
            bbox=descriptor.hull.hitTester.bbox,
            localHitTest=mock.Mock(side_effect=directional_hit))
        descriptor.hull.materials = {7: front, 8: side}
        vehicle = _Vehicle(
            11, descriptor, _Vector(), (0.0, 0.0, 0.0),
            {'health': 500})

        front_armor = battle._native_ram_vehicle_armor(
            vehicle, vehicle.matrix, _Vector(0.0, 0.0, 3.4),
            (0.0, -1.0))
        side_armor = battle._native_ram_vehicle_armor(
            vehicle, vehicle.matrix, _Vector(1.4, 0.0, 0.0),
            (-1.0, 0.0))

        self.assertEqual(100.0, front_armor['armor'])
        self.assertEqual(80.0, side_armor['armor'])

    def test_native_ram_plate_does_not_probe_past_the_center_plane(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        vehicle = _Vehicle(
            11, _Descriptor(), _Vector(), (0.0, 0.0, 0.0),
            {'health': 500})

        with mock.patch.object(
                battle_runtime_module, 'collide_vehicle_at_matrix') as probe:
            armor = battle._native_ram_vehicle_armor(
                vehicle, vehicle.matrix, _Vector(0.0, 0.0, 1.0),
                (0.0, 1.0))

        self.assertIsNone(armor)
        probe.assert_not_called()

    def test_native_ram_plate_skips_external_screen_to_structure(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        descriptor = _Descriptor()
        screen = types.SimpleNamespace(
            armor=20.0, vehicleDamageFactor=0.0)
        hull = types.SimpleNamespace(
            armor=73.0, vehicleDamageFactor=1.0)
        roof = types.SimpleNamespace(
            armor=20.0, vehicleDamageFactor=1.0)
        descriptor.chassis.hitTester = types.SimpleNamespace(
            bbox=descriptor.chassis.hitTester.bbox,
            localHitTest=mock.Mock(return_value=[
                (0.5, None, 1.0, 1)]))
        descriptor.chassis.materials = {1: screen}
        descriptor.hull.hitTester = types.SimpleNamespace(
            bbox=descriptor.hull.hitTester.bbox,
            localHitTest=mock.Mock(return_value=[
                (1.0, None, 1.0, 7)]))
        descriptor.hull.materials = {7: hull, 8: roof}
        vehicle = _Vehicle(
            11, descriptor, _Vector(), (0.0, 0.0, 0.0),
            {'health': 500})

        armor = battle._native_ram_vehicle_armor(
            vehicle, vehicle.matrix, _Vector(0.0, 0.0, 3.5),
            (0.0, -1.0))

        self.assertEqual({'armor': 73.0, 'screened': False}, armor)

    def test_worker_bot_ram_probe_uses_both_native_structural_plates(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._worker_mode = True
        first_vehicle = _Vehicle(
            11, _Descriptor(), _Vector(), (0.0, 0.0, 0.0),
            {'health': 500})
        second_vehicle = _Vehicle(
            12, _Descriptor(), _Vector(0.5, 0.0, 0.0),
            (0.0, 0.0, 0.0), {'health': 500})
        runtime.bigworld.entities.update({
            11: first_vehicle, 12: second_vehicle})
        battle._records = {
            'bot:11': {
                'engine_id': 11, 'kind': 'bot', 'ready': True,
                'tombstone': False},
            'bot:12': {
                'engine_id': 12, 'kind': 'bot', 'ready': True,
                'tombstone': False},
        }
        battle._native_ram_vehicle_armor = mock.Mock(side_effect=[
            {'armor': 45.0, 'screened': False},
            {'armor': 80.0, 'screened': False},
        ])
        first = {
            'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'shape': (1.5, 3.5, 0.0, 1.0),
        }
        second = {
            'id': 12, 'x': 0.5, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'shape': (1.5, 3.5, 0.0, 1.0),
        }

        armors = battle._bot_ram_contact_armor(
            first, second, (-1.0, 0.0, 2.5))

        self.assertEqual((45.0, 80.0), armors)
        first_hit = battle._native_ram_vehicle_armor.call_args_list[0].args[2]
        second_hit = battle._native_ram_vehicle_armor.call_args_list[1].args[2]
        self.assertEqual((first_hit.x, first_hit.y, first_hit.z),
                         (second_hit.x, second_hit.y, second_hit.z))
        self.assertAlmostEqual(0.5, first_hit.y)
        first_inward = battle._native_ram_vehicle_armor.call_args_list[
            0].args[3]
        second_inward = battle._native_ram_vehicle_armor.call_args_list[
            1].args[3]
        self.assertEqual((-1.0, 0.0), first_inward)
        self.assertEqual((1.0, -0.0), second_inward)

    def test_worker_publishes_exact_human_ram_probe_with_player_entities(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._worker_mode = True
        first_vehicle = _Vehicle(
            21, _Descriptor('ussr:R11_MS-1'), _Vector(),
            (0.0, 0.0, 0.0), {'health': 500})
        second_vehicle = _Vehicle(
            22, _Descriptor('ussr:R11_MS-1'), _Vector(0.0, 0.0, 0.5),
            (0.0, 0.0, 0.0), {'health': 500})
        runtime.bigworld.entities.update({
            21: first_vehicle, 22: second_vehicle})
        battle._records = {
            'player:1': {
                'engine_id': 21, 'kind': 'player', 'network_id': 1,
                'ready': True, 'tombstone': False,
                'state': {'vehicle': 'ussr:R11_MS-1'},
            },
            'player:2': {
                'engine_id': 22, 'kind': 'player', 'network_id': 2,
                'ready': True, 'tombstone': False,
                'state': {'vehicle': 'ussr:R11_MS-1'},
            },
        }
        battle._last_snapshot = {'human_ram_probes': [{
            'seq': 7,
            'contact_normal': [0.0, -1.0],
            'first': {
                'id': 1, 'vehicle': 'ussr:R11_MS-1',
                'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
                'pitch': 0.1, 'roll': -0.1,
                'shape': [1.5, 3.5, -0.8, 2.0],
            },
            'second': {
                'id': 2, 'vehicle': 'ussr:R11_MS-1',
                'x': 0.0, 'y': 0.0, 'z': 0.5, 'yaw': math.pi,
                'pitch': 0.0, 'roll': 0.0,
                'shape': [1.5, 3.5, -0.8, 2.0],
            },
        }]}
        first_matrix = object()
        second_matrix = object()
        battle._ram_pose_matrix = mock.Mock(
            side_effect=[first_matrix, second_matrix])
        battle._native_ram_vehicle_armor = mock.Mock(side_effect=[
            {'armor': 45.0, 'screened': False},
            {'armor': 80.0, 'screened': False},
        ])
        battle.client = types.SimpleNamespace(
            send_projected_bot_state=mock.Mock(return_value=True))

        self.assertTrue(battle._send_bot_message({
            'type': 'bot_state', 'bots': [], 'sample_time_us': 40000,
            'source_batch_horizon_us': 40000}))

        self.assertEqual([
            mock.call((0.0, 0.0, 0.0), 0.0, 0.1, -0.1),
            mock.call((0.0, 0.0, 0.5), math.pi, 0.0, 0.0),
        ], battle._ram_pose_matrix.call_args_list)
        self.assertIs(first_vehicle,
                      battle._native_ram_vehicle_armor.call_args_list[0][0][0])
        self.assertIs(first_matrix,
                      battle._native_ram_vehicle_armor.call_args_list[0][0][1])
        self.assertIs(second_vehicle,
                      battle._native_ram_vehicle_armor.call_args_list[1][0][0])
        self.assertIs(second_matrix,
                      battle._native_ram_vehicle_armor.call_args_list[1][0][1])
        first_inward = battle._native_ram_vehicle_armor.call_args_list[
            0].args[3]
        second_inward = battle._native_ram_vehicle_armor.call_args_list[
            1].args[3]
        self.assertEqual((0.0, -1.0), first_inward)
        self.assertEqual((-0.0, 1.0), second_inward)
        battle.client.send_projected_bot_state.assert_called_once_with(
            [], sample_time_us=40000,
            source_batch_horizon_us=40000, human_ram_armors=[{
                'seq': 7, 'first_id': 1, 'second_id': 2,
                'available': True, 'armor_first': 45.0,
                'armor_second': 80.0,
            }])

        battle._last_snapshot['human_ram_probes'][0][
            'contact_normal'] = [0.0, 1.0]
        with self.assertRaisesRegex(
                RuntimeError, 'worker human ram probe is invalid'):
            battle._human_ram_armor_results()

    def test_worker_retains_human_ram_probe_until_entities_are_ready(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._worker_mode = True
        battle._last_snapshot = {'human_ram_probes': [{
            'seq': 7,
            'contact_normal': [0.0, -1.0],
            'first': {
                'id': 1, 'vehicle': 'ussr:R11_MS-1',
                'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
                'pitch': 0.0, 'roll': 0.0,
                'shape': [1.5, 3.5, -0.8, 2.0],
            },
            'second': {
                'id': 2, 'vehicle': 'ussr:R11_MS-1',
                'x': 0.0, 'y': 0.0, 'z': 6.5, 'yaw': math.pi,
                'pitch': 0.0, 'roll': 0.0,
                'shape': [1.5, 3.5, -0.8, 2.0],
            },
        }]}
        battle._records = {'player:1': {
            'kind': 'player', 'network_id': 1, 'ready': False,
            'tombstone': False, 'state': {'vehicle': 'ussr:R11_MS-1'},
        }}
        battle._native_ram_vehicle_armor = mock.Mock()

        self.assertEqual([], battle._human_ram_armor_results())
        battle._native_ram_vehicle_armor.assert_not_called()

        first_vehicle = _Vehicle(
            21, _Descriptor('ussr:R11_MS-1'), _Vector(),
            (0.0, 0.0, 0.0), {'health': 500})
        second_vehicle = _Vehicle(
            22, _Descriptor('ussr:R11_MS-1'), _Vector(0.0, 0.0, 6.5),
            (0.0, 0.0, 0.0), {'health': 500})
        runtime.bigworld.entities.update({
            21: first_vehicle, 22: second_vehicle})
        battle._records = {
            'player:1': {
                'engine_id': 21, 'kind': 'player', 'network_id': 1,
                'ready': True, 'tombstone': False,
                'state': {'vehicle': 'ussr:R11_MS-1'},
            },
            'player:2': {
                'engine_id': 22, 'kind': 'player', 'network_id': 2,
                'ready': True, 'tombstone': False,
                'state': {'vehicle': 'ussr:R11_MS-1'},
            },
        }
        battle._native_ram_vehicle_armor = mock.Mock(return_value=None)

        self.assertEqual([{
            'seq': 7, 'first_id': 1, 'second_id': 2,
            'available': False,
        }], battle._human_ram_armor_results())
        battle._native_ram_vehicle_armor.assert_called_once()

    def test_native_ram_callbacks_dedupe_one_sustained_contact_episode(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._local_position = (0.0, 0.0, 0.0)
        battle._local_yaw = 0.0
        battle._estimated_motion_time_us = mock.Mock(return_value=100000)
        battle._retry_native_ram_contact_proof = mock.Mock(return_value=True)
        local = _Vehicle(
            10, _Descriptor(), _Vector(), (0.0, 0.0, 0.0),
            {'health': 500})
        local.filter.velocity = _Vector(0.0, 0.0, 10.0)
        remote = _Vehicle(
            11, _Descriptor(), _Vector(0.0, 0.0, 6.5),
            (0.0, 0.0, 0.0), {'health': 500})
        remote.filter.velocity = _Vector(0.0, 0.0, 0.0)
        battle._records = {'bot:11': {
            'engine_id': 11, 'network_id': 11, 'kind': 'bot',
            'ready': True}}

        battle._observe_native_ram_contact(
            local, remote, _Vector(0.0, 0.0, 3.25), 10.0)
        battle._poll_local_ram_contact_episodes(object(), {
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'vx': 0.0, 'vy': 0.0, 'vz': 10.0,
            'shape': (1.5, 3.5, 0.0, 1.0),
            'ram_profile': {
                'spall_coefficient': 1.0, 'ramming_bonus': 0.0},
        }, ({
            'network_id': 11, 'kind': 'bot', 'alive': True,
            'x': 0.0, 'y': 0.0, 'z': 20.0, 'yaw': 0.0,
            'vx': 0.0, 'vy': 0.0, 'vz': 0.0,
            'shape': (1.5, 3.5, 0.0, 1.0),
        },))
        battle._observe_native_ram_contact(
            local, remote, _Vector(0.0, 0.0, 3.25), 10.1)

        self.assertEqual([mock.call(1)],
                         battle._retry_native_ram_contact_proof.call_args_list)
        self.assertEqual(1, len(battle._native_ram_contact_proofs))

    def test_native_ram_plate_fails_closed_after_external_only(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        descriptor = _Descriptor()
        screen = types.SimpleNamespace(
            armor=20.0, vehicleDamageFactor=0.0)
        hull = types.SimpleNamespace(
            armor=100.0, vehicleDamageFactor=1.0)
        roof = types.SimpleNamespace(
            armor=20.0, vehicleDamageFactor=1.0)
        descriptor.chassis.hitTester = types.SimpleNamespace(
            bbox=descriptor.chassis.hitTester.bbox,
            localHitTest=mock.Mock(return_value=[
                (0.5, None, 1.0, 1)]))
        descriptor.chassis.materials = {1: screen}
        descriptor.hull.hitTester = types.SimpleNamespace(
            bbox=descriptor.hull.hitTester.bbox,
            localHitTest=mock.Mock(return_value=[]))
        descriptor.hull.materials = {7: hull, 8: roof}
        vehicle = _Vehicle(
            11, descriptor, _Vector(), (0.0, 0.0, 0.0),
            {'health': 500})

        with mock.patch.object(
                combat_rules, 'he_hull_armor',
                side_effect=AssertionError('global hull fallback used')):
            armor = battle._native_ram_vehicle_armor(
                vehicle, vehicle.matrix, _Vector(0.0, 0.0, 3.5),
                (0.0, -1.0))

        self.assertIsNone(armor)

    def test_native_ram_plate_fails_closed_without_any_layer(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        descriptor = _Descriptor()
        descriptor.hull.materials = {7: types.SimpleNamespace(
            armor=73.0, vehicleDamageFactor=1.0)}
        vehicle = _Vehicle(
            11, descriptor, _Vector(), (0.0, 0.0, 0.0),
            {'health': 500})

        armor = battle._native_ram_vehicle_armor(
            vehicle, vehicle.matrix, _Vector(0.0, 0.0, 3.5),
            (0.0, -1.0))

        self.assertIsNone(armor)

    def test_native_ram_hook_preserves_and_restores_player_avatar_method(self):
        calls = []

        class Avatar(object):
            def handleVehicleCollidedVehicle(
                    self, veh_a, veh_b, hit_point, contact_time):
                calls.append(('native', contact_time))
                return 'native-result'

        battle = BattleRuntime(_runtime())
        battle._avatar = Avatar()
        battle._worker_mode = False
        battle._observe_native_ram_contact = mock.Mock()
        original = Avatar.__dict__['handleVehicleCollidedVehicle']

        self.assertTrue(battle._install_native_ram_contact_hook())
        self.assertEqual('native-result',
                         battle._avatar.handleVehicleCollidedVehicle(
                             'a', 'b', 'hit', 4.0))
        self.assertEqual([('native', 4.0)], calls)
        battle._observe_native_ram_contact.assert_called_once_with(
            'a', 'b', 'hit', 4.0)
        self.assertTrue(battle._restore_native_ram_contact_hook())
        self.assertIs(original,
                      Avatar.__dict__['handleVehicleCollidedVehicle'])

    def test_ram_history_keeps_wire_pose_not_integrated_authority_pose(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._bots = types.SimpleNamespace(states={11: {
            'id': 11, 'x': 99.0, 'y': 0.0, 'z': 98.0, 'yaw': 1.0,
            'speed': 8.0, 'push_x': 3.0, 'push_z': 4.0,
            'mass': 28000.0, 'collision_shape': (1.5, 3.5, 0.0, 1.0),
            'vehicle': 'ussr:T-34', 'team': 2,
        }})

        self.assertTrue(battle._remember_ram_bot_snapshot({
            'bot_state_revision': 37,
            'bot_state_time_us': 370000,
            'bots': [{'id': 11, 'x': 1.0, 'y': 2.0, 'z': 3.0,
                      'yaw': 0.25, 'speed': 4.0, 'alive': True}],
        }))

        historical = battle._ram_bot_history[37][11]
        self.assertEqual((1.0, 2.0, 3.0, 0.25, 4.0), (
            historical['x'], historical['y'], historical['z'],
            historical['yaw'], historical['speed']))
        self.assertEqual(28000.0, historical['mass'])
        self.assertNotIn('push_x', historical)
        self.assertNotIn('push_z', historical)

    def test_ram_history_interpolates_presented_wire_time_and_velocity(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._bots = types.SimpleNamespace(states={11: {
            'id': 11, 'mass': 28000.0,
            'collision_shape': (1.5, 3.5, 0.0, 1.0),
            'vehicle': 'ussr:T-34', 'team': 2,
        }})
        for revision, sample_time, z in (
                (36, 100000, 0.0), (37, 200000, 1.6)):
            self.assertTrue(battle._remember_ram_bot_snapshot({
                'bot_state_revision': revision,
                'bot_state_time_us': sample_time,
                'bots': [{'id': 11, 'x': 0.0, 'y': 0.0, 'z': z,
                          'yaw': 0.0, 'alive': True}],
            }))

        historical = battle._ram_bot_state_at(11, 37, 150000)

        self.assertAlmostEqual(0.8, historical['z'])
        self.assertAlmostEqual(16.0, historical['ram_vz'])
        self.assertAlmostEqual(
            0.8, battle._ram_bot_state_at(11, 36, 150000)['z'])
        self.assertIsNone(battle._ram_bot_state_at(11, 37, 250000))
        self.assertEqual(37, battle._ram_bot_revision_at(11, 150000))
        self.assertEqual(37, battle._ram_bot_revision_at(11, 200000))
        self.assertIsNone(battle._ram_bot_revision_at(11, 50000))
        self.assertIsNone(battle._ram_bot_revision_at(11, 250000))

    def test_ram_history_caches_repeated_receipt_decoration(self):
        class IterationForbiddenHistory(list):

            def __iter__(self):
                raise AssertionError('RAM lookup scanned revision history')

        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._bots = types.SimpleNamespace(states={11: {
            'id': 11, 'mass': 28000.0,
            'collision_shape': (1.5, 3.5, 0.0, 1.0),
            'vehicle': 'ussr:T-34', 'team': 2,
        }})
        for revision, sample_time, z in (
                (36, 100000, 0.0), (37, 200000, 1.6)):
            self.assertTrue(battle._remember_ram_bot_snapshot({
                'bot_state_revision': revision,
                'bot_state_time_us': sample_time,
                'bots': [{'id': 11, 'x': 0.0, 'y': 0.0, 'z': z,
                          'yaw': 0.0, 'alive': True}],
            }))
        battle._ram_bot_history_order = IterationForbiddenHistory(
            battle._ram_bot_history_order)
        player = {'ram_contacts': [{
            'seq': 1, 'bot_id': 11, 'bot_state_revision': 37,
            'presentation_time_us': 150000,
        }]}
        original_left = battle_runtime_module.bisect.bisect_left
        original_right = battle_runtime_module.bisect.bisect_right

        with mock.patch.object(
                battle_runtime_module.bisect, 'bisect_left',
                side_effect=original_left) as left_spy, mock.patch.object(
                    battle_runtime_module.bisect, 'bisect_right',
                    side_effect=original_right) as right_spy:
            first = battle._decorate_ram_contacts(player)
            first_calls = (left_spy.call_count, right_spy.call_count)
            second = battle._decorate_ram_contacts(player)

        self.assertEqual((1, 1), first_calls)
        self.assertEqual(
            first_calls, (left_spy.call_count, right_spy.call_count))
        self.assertAlmostEqual(
            0.8, first['ram_contacts'][0]['_ram_contact_bot_state']['z'])
        self.assertEqual(first, second)

    def test_ram_history_advance_invalidates_cached_exact_sample(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._bots = types.SimpleNamespace(states={11: {
            'id': 11, 'mass': 28000.0,
            'collision_shape': (1.5, 3.5, 0.0, 1.0),
            'vehicle': 'ussr:T-34', 'team': 2,
        }})
        self.assertTrue(battle._remember_ram_bot_snapshot({
            'bot_state_revision': 37,
            'bot_state_time_us': 200000,
            'bots': [{'id': 11, 'x': 0.0, 'y': 0.0, 'z': 1.0,
                      'yaw': 0.0, 'alive': True}],
        }))
        key = (11, 37, 200000)

        first = battle._ram_bot_state_at(*key)

        self.assertEqual(0.0, first['ram_vz'])
        self.assertIn(key, battle._ram_bot_lookup_cache)
        self.assertTrue(battle._remember_ram_bot_snapshot({
            'bot_state_revision': 38,
            'bot_state_time_us': 300000,
            'bots': [{'id': 11, 'x': 0.0, 'y': 0.0, 'z': 3.0,
                      'yaw': 0.0, 'alive': True}],
        }))
        self.assertNotIn(key, battle._ram_bot_lookup_cache)

        advanced = battle._ram_bot_state_at(*key)

        self.assertAlmostEqual(20.0, advanced['ram_vz'])

    def test_ram_history_replaces_repeated_revision_in_ordered_index(self):
        battle = BattleRuntime(_runtime())
        battle._bots = types.SimpleNamespace(states={11: {}})
        message = {
            'bot_state_revision': 37,
            'bot_state_time_us': 200000,
            'bots': [{'id': 11, 'x': 0.0, 'y': 0.0, 'z': 1.0,
                      'yaw': 0.0, 'alive': True}],
        }
        self.assertTrue(battle._remember_ram_bot_snapshot(message))
        key = (11, 37, 200000)
        self.assertEqual(1.0, battle._ram_bot_state_at(*key)['z'])

        replacement = dict(message)
        replacement['bots'] = [dict(message['bots'][0], z=2.0)]
        self.assertTrue(battle._remember_ram_bot_snapshot(replacement))

        self.assertEqual([37], battle._ram_bot_history_order)
        self.assertEqual([(200000, 37)],
                         battle._ram_bot_history_index[11])
        self.assertEqual(2.0, battle._ram_bot_state_at(*key)['z'])

    def test_new_round_clears_ram_history_index_and_lookup_cache(self):
        battle = BattleRuntime(_runtime())
        battle._ram_bot_history = {37: {11: {'id': 11}}}
        battle._ram_bot_history_order = [37]
        battle._ram_bot_history_times = {37: 200000}
        battle._ram_bot_history_index = {11: [(200000, 37)]}
        battle._ram_bot_lookup_cache = {(11, 37, 200000): {'id': 11}}

        with mock.patch.object(battle, '_standard_arena', return_value=None):
            self.assertFalse(battle.start(
                {'map': '01_karelia'}, {}, _Client()))

        self.assertEqual({}, battle._ram_bot_history)
        self.assertEqual([], battle._ram_bot_history_order)
        self.assertEqual({}, battle._ram_bot_history_times)
        self.assertEqual({}, battle._ram_bot_history_index)
        self.assertEqual({}, battle._ram_bot_lookup_cache)

    def test_ram_history_brackets_a_coalesced_exact_revision(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._bots = types.SimpleNamespace(states={11: {
            'id': 11, 'mass': 28000.0,
            'collision_shape': (1.5, 3.5, 0.0, 1.0),
            'vehicle': 'ussr:T-34', 'team': 2,
        }})
        for revision, sample_time, z in (
                (10, 100000, 0.0), (12, 120000, 2.0)):
            self.assertTrue(battle._remember_ram_bot_snapshot({
                'bot_state_revision': revision,
                'bot_state_time_us': sample_time,
                'bots': [{'id': 11, 'x': 0.0, 'y': 0.0, 'z': z,
                          'yaw': 0.0, 'alive': True}],
            }))

        historical = battle._ram_bot_state_at(11, 11, 110000)

        self.assertAlmostEqual(1.0, historical['z'])
        self.assertAlmostEqual(100.0, historical['ram_vz'])

    def test_ram_wire_history_is_bounded_with_its_sample_times(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._bots = types.SimpleNamespace(states={11: {
            'id': 11, 'mass': 28000.0,
            'collision_shape': (1.5, 3.5, 0.0, 1.0),
            'vehicle': 'ussr:T-34', 'team': 2,
        }})
        for revision in range(516):
            self.assertTrue(battle._remember_ram_bot_snapshot({
                'bot_state_revision': revision,
                'bot_state_time_us': revision * 40000,
                'bots': [{'id': 11, 'x': 0.0, 'y': 0.0,
                          'z': revision * 0.1, 'yaw': 0.0,
                          'alive': True}],
            }))

        self.assertEqual(512, len(battle._ram_bot_history_order))
        self.assertEqual(512, len(battle._ram_bot_history))
        self.assertEqual(512, len(battle._ram_bot_history_times))
        self.assertEqual(512, len(battle._ram_bot_history_index[11]))
        self.assertEqual((160000, 4),
                         battle._ram_bot_history_index[11][0])
        self.assertNotIn(3, battle._ram_bot_history)
        self.assertNotIn(3, battle._ram_bot_history_times)
        self.assertIn(4, battle._ram_bot_history)
        key = (11, 4, 160000)
        self.assertIsNotNone(battle._ram_bot_state_at(*key))
        self.assertIn(key, battle._ram_bot_lookup_cache)

        self.assertTrue(battle._remember_ram_bot_snapshot({
            'bot_state_revision': 516,
            'bot_state_time_us': 516 * 40000,
            'bots': [{'id': 11, 'x': 0.0, 'y': 0.0,
                      'z': 51.6, 'yaw': 0.0, 'alive': True}],
        }))

        self.assertNotIn(4, battle._ram_bot_history)
        self.assertEqual(512, len(battle._ram_bot_history_index[11]))
        self.assertEqual((200000, 5),
                         battle._ram_bot_history_index[11][0])
        self.assertNotIn(key, battle._ram_bot_lookup_cache)
        self.assertIsNone(battle._ram_bot_state_at(*key))

    def test_live_bot_contact_applies_local_half_for_both_teams(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        local = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                         {'health': 500})
        remote = _Vehicle(11, _Descriptor(), _Vector(0.0, 0.0, 6.5),
                          (0, 0, 0), {'health': 500})
        runtime.bigworld.entities[11] = remote
        state = {
            'x': 0.0, 'y': 0.0, 'z': 6.5, 'yaw': math.pi,
            'speed': 5.0, 'alive': True, 'team': 1,
        }
        battle._records = {'bot:11': {
            'engine_id': 11, 'network_id': 11, 'kind': 'bot',
            'local': False, 'ready': True, 'tombstone': False,
            'state': state,
        }}
        battle._bots = types.SimpleNamespace(states={11: state})
        battle._local_physics = {'mass': 25000.0}
        battle._local_speed = 0.0
        battle._local_push_x = 0.0
        battle._local_push_z = 0.0
        battle._motion_is_clear = mock.Mock(return_value=True)
        battle._baked_pose_safe = mock.Mock(return_value=True)

        friendly_position = battle._resolve_local_tank_contacts(
            local, (0.0, 0.0, 0.0), 0.0, 0.1)
        friendly_push = battle._local_push_z

        state['team'] = 2
        battle._local_speed = 0.0
        battle._local_push_x = 0.0
        battle._local_push_z = 0.0
        enemy_position = battle._resolve_local_tank_contacts(
            local, (0.0, 0.0, 0.0), 0.0, 0.1)
        enemy_push = battle._local_push_z

        self.assertLess(friendly_position[2], 0.0)
        self.assertLess(friendly_push, 0.0)
        self.assertAlmostEqual(friendly_push, enemy_push)
        self.assertAlmostEqual(friendly_position[2], enemy_position[2])

        # A dead Bot is an immovable wreck, not a live teammate ownership
        # exception.  Its contact response must not depend on team colour.
        state['alive'] = False
        dead_results = []
        for team in (1, 2):
            state['team'] = team
            battle._local_speed = 0.0
            battle._local_push_x = 0.0
            battle._local_push_z = 0.0
            dead_position = battle._resolve_local_tank_contacts(
                local, (0.0, 0.0, 0.0), 0.0, 0.1)
            dead_results.append((
                dead_position, battle._local_speed,
                battle._local_push_x, battle._local_push_z))
        self.assertEqual(dead_results[0], dead_results[1])

    def test_local_push_decay_is_equal_across_render_rates(self):
        def run_for_one_second(frame_rate):
            runtime = _runtime()
            battle = BattleRuntime(runtime)
            battle._avatar = runtime.bigworld.avatar
            battle._records = {}
            battle._local_physics = {'mass': 25000.0}
            battle._local_speed = 0.0
            battle._local_push_x = 10.0
            battle._local_push_z = -4.0
            battle._motion_is_clear = mock.Mock(return_value=True)
            battle._baked_pose_safe = mock.Mock(return_value=True)
            entity = _Vehicle(
                10, _Descriptor(), _Vector(), (0, 0, 0), {'health': 500})
            position = (0.0, 0.0, 0.0)
            dt = 1.0 / float(frame_rate)
            first_push = None
            for unused_frame in range(frame_rate):
                position = battle._resolve_local_tank_contacts(
                    entity, position, 0.0, dt)
                if first_push is None:
                    first_push = battle._local_push_x
            return first_push, battle._local_push_x

        results = dict((frame_rate, run_for_one_second(frame_rate))
                       for frame_rate in (20, 30, 60))
        expected = 10.0 * 0.90 ** 60

        self.assertAlmostEqual(9.0, results[60][0], places=12)
        for unused_frame_rate, (unused_first, final_push) in results.items():
            self.assertAlmostEqual(expected, final_push, places=12)
        self.assertAlmostEqual(results[20][1], results[30][1], places=12)
        self.assertAlmostEqual(results[30][1], results[60][1], places=12)

    def test_local_tank_contact_cannot_push_hull_through_world_geometry(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        local = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                         {'health': 500})
        remote = _Vehicle(11, _Descriptor(), _Vector(0.0, 0.0, 6.5),
                          (0, 0, 0), {'health': 500})
        runtime.bigworld.entities[11] = remote
        battle._records = {'player:2': {
            'engine_id': 11, 'network_id': 2, 'kind': 'player',
            'local': False, 'ready': True, 'tombstone': False,
            'state': {'x': 0.0, 'y': 0.0, 'z': 6.5, 'yaw': 0.0,
                      'speed': 0.0, 'alive': True,
                      'effective_params': _effective_params_snapshot()}}}
        battle._local_physics = {'mass': 25000.0}
        battle._local_speed = 5.0
        battle._motion_is_clear = mock.Mock(return_value=False)
        battle._baked_pose_safe = mock.Mock(return_value=True)

        position = battle._resolve_local_tank_contacts(
            local, (0.0, 0.0, 0.0), 0.0, 0.1)

        self.assertEqual((0.0, 0.0, 0.0), position)
        self.assertEqual(0.0, battle._local_push_x)
        self.assertEqual(0.0, battle._local_push_z)
        battle._baked_pose_safe.assert_not_called()

    def test_active_exception_prints_original_traceback_before_cleanup(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._cleanup = lambda: None
        runtime.compatibility.restore_lobby_account = lambda: None
        battle.client = types.SimpleNamespace(on_event=None)
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            try:
                raise RuntimeError('native operation denied')
            except RuntimeError as error:
                battle._fail(error)
            runtime.bigworld.callbacks.pop()()

        rendered = output.getvalue()
        self.assertIn('battle failed: native operation denied', rendered)
        self.assertIn('battle traceback:', rendered)
        self.assertIn('RuntimeError: native operation denied', rendered)
        self.assertIn(
            'test_active_exception_prints_original_traceback_before_cleanup',
            rendered)

    def test_selected_commander_sixth_sense_is_read_before_lobby_retire(self):
        tankman = types.SimpleNamespace(skills=(
            types.SimpleNamespace(name='commander_sixthSense'),))
        current_vehicle = types.ModuleType('CurrentVehicle')
        current_vehicle.g_currentVehicle = types.SimpleNamespace(
            item=types.SimpleNamespace(crew=((0, tankman),)))

        with mock.patch.dict(sys.modules, {
                'CurrentVehicle': current_vehicle}):
            self.assertTrue(_selected_vehicle_has_sixth_sense())

    def test_battle_ammo_reads_the_mounted_1513_garage_items(self):
        # #1513 gui_items.Vehicle carries Shell items and a VehicleEquipment,
        # not the 0.8.2 shellsLayout mapping and eqs list.
        battle = BattleRuntime(_runtime())
        current_vehicle = types.ModuleType('CurrentVehicle')
        current_vehicle.g_currentVehicle = types.SimpleNamespace(
            isPresent=lambda: True,
            item=types.SimpleNamespace(
                shells=[types.SimpleNamespace(intCD=101, count=30),
                        types.SimpleNamespace(intCD=102, count=12)],
                equipment=types.SimpleNamespace(
                    regularConsumables=_Consumables([401, 0, 403]))))

        with mock.patch.dict(sys.modules, {
                'CurrentVehicle': current_vehicle}):
            self.assertEqual({101: 30, 102: 12}, battle._local_ammo_layout())
            self.assertEqual(
                [401, 0, 403], battle._local_mounted_equipments())

        # Retiring the lobby Account empties g_currentVehicle, so the battle
        # must keep reading the snapshot captured before that boundary.
        current_vehicle.g_currentVehicle = types.SimpleNamespace(
            isPresent=lambda: False, item=None)
        with mock.patch.dict(sys.modules, {
                'CurrentVehicle': current_vehicle}):
            self.assertEqual({101: 30, 102: 12}, battle._local_ammo_layout())
            self.assertEqual(
                [401, 0, 403], battle._local_mounted_equipments())

    def test_battle_ammo_falls_back_without_a_garage_item(self):
        battle = BattleRuntime(_runtime())
        current_vehicle = types.ModuleType('CurrentVehicle')
        current_vehicle.g_currentVehicle = types.SimpleNamespace(
            isPresent=lambda: False, item=None)

        with mock.patch.dict(sys.modules, {
                'CurrentVehicle': current_vehicle}):
            self.assertIsNone(battle._local_ammo_layout())
            self.assertIsNone(battle._local_mounted_equipments())

    def test_battle_ammo_preserves_an_explicit_empty_garage_layout(self):
        battle = BattleRuntime(_runtime())
        current_vehicle = types.ModuleType('CurrentVehicle')
        current_vehicle.g_currentVehicle = types.SimpleNamespace(
            isPresent=lambda: True,
            item=types.SimpleNamespace(shells=[], equipment=None))

        with mock.patch.dict(sys.modules, {
                'CurrentVehicle': current_vehicle}):
            self.assertEqual({}, battle._local_ammo_layout())

    def test_mounted_rations_are_published_without_an_activation_action(self):
        runtime = _runtime()
        descriptor = types.SimpleNamespace(
            id=(11, 17), compactDescr=401, name='cola', tags=(),
            cooldownSeconds=0.0, reuseCount=-1,
            crewLevelIncrease=10.0)
        runtime.vehicles.g_cache.equipments = lambda: {401: descriptor}
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._local_mounted_equipments = lambda: [401]

        state = battle._default_equipments()
        battle._equipment_state = state
        battle._present_equipments(now=0.0)

        self.assertIsInstance(state[0], equipment_mechanics.EquipmentState)
        self.assertEqual('stimulator', state[0].contract['kind'])
        self.assertEqual(
            (10, 401, 1, runtime.constants.EQUIPMENT_STAGES.READY, 0),
            runtime.bigworld.avatar.ammo_updates[-1])
        self.assertFalse(battle._activate_equipment(17))

    def test_bot_consumables_are_resolved_from_the_exact_client_cache(self):
        runtime = _runtime()
        descriptors = {}
        identifiers = {}
        for index, name in enumerate(
                equipment_mechanics.DEFAULT_BOT_CONSUMABLE_NAMES, 1):
            identifiers[name] = index
            descriptors[index] = types.SimpleNamespace(
                id=(11, 20 + index), compactDescr=420 + index,
                name=name,
                tags=(('medkit',) if name == 'largeMedkit' else
                      ('repairkit',) if name == 'largeRepairkit' else ()),
                reuseCount=1, cooldownSeconds=90.0,
                autoactivate=name == 'autoExtinguishers',
                repairAll=name != 'autoExtinguishers',
                fireStartingChanceFactor=(
                    0.9 if name == 'autoExtinguishers' else 1.0))
        runtime.vehicles.g_cache = types.SimpleNamespace(
            equipmentIDs=lambda: identifiers,
            equipments=lambda: descriptors)
        battle = BattleRuntime(runtime)

        contracts = battle._default_bot_equipment_contracts()

        self.assertEqual(
            equipment_mechanics.DEFAULT_BOT_CONSUMABLE_NAMES,
            tuple(value['name'] for value in contracts))
        self.assertEqual(
            ('extinguisher', 'medkit', 'repairkit'),
            tuple(value['kind'] for value in contracts))

    def test_removed_rpm_limiter_is_not_a_permanent_passive_factor(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        limiter = types.SimpleNamespace(name='removedRpmLimiter')
        oil = types.SimpleNamespace(name='lendLeaseOil')
        battle._garage_loadout = {
            'crew': (), 'equipments': (limiter, oil)}

        with mock.patch.object(
                battle_runtime_module.loadout_law, 'attribute_factors',
                return_value={'engine/power': 1.05}) as factors:
            self.assertEqual(
                {'engine/power': 1.05},
                battle._local_factors(_Descriptor()))

        self.assertEqual((oil,), factors.call_args[0][2])

    def test_removed_rpm_limiter_uses_exact_descriptor_trigger_values(self):
        runtime = _runtime()
        descriptor = types.SimpleNamespace(
            id=(11, 12), compactDescr=401,
            name='removedRpmLimiter', tags=('trigger',),
            cooldownSeconds=0.0, reuseCount=0,
            enginePowerFactor=1.1, engineHpLossPerSecond=1.5)
        runtime.vehicles.g_cache.equipments = lambda: {401: descriptor}
        battle = BattleRuntime(runtime)
        battle._local_mounted_equipments = lambda: [401]

        state = battle._default_equipments()

        self.assertEqual('rpm_limiter', state[0].contract['kind'])
        self.assertEqual(1, state[0].uses_left)
        self.assertEqual(1.1, state[0].contract['enginePowerFactor'])
        self.assertEqual(
            1.5, state[0].contract['engineHpLossPerSecond'])

    def test_worker_replica_rebuilds_equipment_from_canonical_snapshot(self):
        descriptor = types.SimpleNamespace(
            id=(11, 41), compactDescr=441,
            name='smallRepairkit', tags=('repairkit',), reuseCount=1,
            cooldownSeconds=10.0, repairAll=False)
        canonical = equipment_mechanics.EquipmentState(
            equipment_mechanics.project_equipment(descriptor), now=100.0)
        self.assertIsNotNone(canonical.activate(
            100.0,
            critical={
                'devices': [{
                    'name': 'engineHealth', 'state': 'destroyed'}],
                'destroyed': ['engineHealth'],
            },
            selected='engineHealth'))
        snapshot = canonical.snapshot(103.0)

        battle = BattleRuntime(_runtime())
        battle.client = types.SimpleNamespace(player_id=7)
        battle._worker_mode = True
        battle._clock = lambda: 500.0
        battle._equipment_revision = -1

        self.assertTrue(battle._restore_local_equipment_snapshot({
            'players': [{
                'id': 7, 'equipment_revision': 4,
                'equipment_states': [snapshot],
            }],
        }))

        restored = battle._equipment_state[0]
        self.assertEqual(1, restored.uses_left)
        self.assertAlmostEqual(507.0, restored.ready_at)
        self.assertEqual(snapshot, restored.snapshot(500.0))
        self.assertEqual(4, battle._equipment_revision)

    def test_critical_proposal_uses_target_equipment_snapshot_factors(self):
        extinguisher = types.SimpleNamespace(
            id=(11, 21), compactDescr=421,
            name='autoExtinguishers', tags=(), reuseCount=0,
            cooldownSeconds=90.0, autoactivate=True,
            fireStartingChanceFactor=0.8)
        medkit = types.SimpleNamespace(
            id=(11, 22), compactDescr=422,
            name='largeMedkit', tags=('medkit',), reuseCount=0,
            cooldownSeconds=90.0, repairAll=True, bonusValue=0.30)
        snapshots = [
            equipment_mechanics.EquipmentState(
                equipment_mechanics.project_equipment(value)).snapshot()
            for value in (extinguisher, medkit)]
        battle = BattleRuntime(_runtime())
        entity = types.SimpleNamespace()
        record = {
            'kind': 'bot', 'network_id': 3,
            'state': {'equipment_states': snapshots},
        }

        self.assertTrue(battle._install_critical_equipment_effects(
            record, entity))
        self.assertAlmostEqual(
            0.8, entity._fire_starting_chance_factor)
        self.assertAlmostEqual(0.30, entity._medkit_bonus_value)

    def test_removed_rpm_limiter_toggles_stock_trigger_stage(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._avatar = runtime.bigworld.avatar
        send_intent = mock.Mock(side_effect=(1, 2))
        battle.client = types.SimpleNamespace(
            player_id=1, send_equipment_intent=send_intent)
        descriptor = _Descriptor()
        entity = _Vehicle(10, descriptor, _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._records = {'player:1': {
            'engine_id': 10, 'state': {'health': 500, 'alive': True},
            'kind': 'player', 'network_id': 1, 'local': True}}
        limiter = types.SimpleNamespace(
            id=(11, 12), compactDescr=401,
            name='removedRpmLimiter', tags=('trigger',),
            cooldownSeconds=0.0, reuseCount=0,
            enginePowerFactor=1.1, engineHpLossPerSecond=1.5)
        battle._equipment_state = [equipment_mechanics.EquipmentState(
            equipment_mechanics.project_equipment(limiter))]
        battle._clock = lambda: 1000.0

        self.assertTrue(battle._activate_equipment((1 << 16) | 12))
        self.assertEqual(1.0, battle._active_engine_power_factor())
        self.assertTrue(battle._activate_equipment(12))
        self.assertEqual(1.0, battle._active_engine_power_factor())
        self.assertEqual([
            mock.call(12, activation_code=(1 << 16) | 12,
                      selected=None, requested_active=True),
            mock.call(12, activation_code=12,
                      selected=None, requested_active=False),
        ], send_intent.call_args_list)
        self.assertFalse(battle._equipment_state[0].active)

    def test_active_removed_rpm_limiter_damages_engine_at_exact_rate(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._battle_live = True
        battle.client = types.SimpleNamespace(player_id=1)
        descriptor = _Descriptor()
        descriptor.engine = {'maxHealth': 100, 'maxRegenHealth': 50}
        entity = _Vehicle(10, descriptor, _Vector(), (0, 0, 0),
                          {'health': 500})
        entity.devices_hp = {}
        entity._destroyed_devices = set()
        entity._crew_ko = set()
        entity.is_on_fire = False
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        record = {
            'engine_id': 10, 'state': {'health': 500, 'alive': True},
            'kind': 'player', 'network_id': 1, 'local': True}
        battle._records = {'player:1': record}
        limiter = types.SimpleNamespace(
            id=(11, 12), compactDescr=401,
            name='removedRpmLimiter', tags=('trigger',), reuseCount=0,
            cooldownSeconds=0.0, enginePowerFactor=1.1,
            engineHpLossPerSecond=1.5)
        limiter_state = equipment_mechanics.EquipmentState(
            equipment_mechanics.project_equipment(limiter))
        limiter_state.active = True
        battle._equipment_state = [limiter_state]
        battle._present_critical = mock.Mock(return_value=True)

        self.assertFalse(battle._tick_rpm_limiter(
            record, entity, 2.0, 1000.0))

        self.assertEqual({}, entity.devices_hp)
        self.assertNotIn('critical_state', record)
        self.assertIsNone(battle._local_damage_report)

    def _shell_change_battle(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        state = gun_mechanics.GunState(
            _two_shell_descriptor(), ammo_layout={101: 20, 102: 10})
        state.reload_time = 0.0
        state.clip = 1
        battle._gun_state = state
        battle._sender = types.SimpleNamespace(
            send_current=mock.Mock(return_value=True))
        battle._publish_ammo_state = mock.Mock()
        battle._publish_reload_event = mock.Mock()
        return battle, state, runtime.constants.VEHICLE_SETTING

    def _pending_fire_shell_change_battle(self, clip_size=1, clip=None):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()
        descriptor = _Descriptor()
        descriptor.gun.clip = (clip_size, 1.0)
        second_shot = copy.copy(descriptor.gun.shots[0])
        second_shot.shell = copy.copy(second_shot.shell)
        second_shot.shell.compactDescr = 102
        descriptor.gun.shots.append(second_shot)
        entity = _Vehicle(
            10, descriptor, _Vector(0, 0, 0), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities[10] = entity
        record = {
            'engine_id': 10, 'state': {'health': 500, 'alive': True},
            'kind': 'player', 'network_id': 1, 'local': True}
        battle.client = client
        battle.state = 'running'
        battle._battle_live = True
        battle._avatar = runtime.bigworld.avatar
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = _LANInputSender(battle)
        battle._start_message = {'round_id': 7}
        battle._records = {'player:1': record}
        battle._gun_state = gun_mechanics.GunState(
            descriptor, ammo_layout={101: 20, 102: 10})
        battle._gun_state.reload_time = 0.0
        battle._gun_state.clip = clip_size if clip is None else clip
        battle._roll_loader_intuition = mock.Mock(return_value=False)
        return (
            battle, battle._gun_state,
            runtime.constants.VEHICLE_SETTING, client, record)

    def test_next_shell_setting_waits_for_the_loaded_round(self):
        battle, state, settings = self._shell_change_battle()

        self.assertTrue(
            battle.change_vehicle_setting(settings.NEXT_SHELLS, 102))

        self.assertEqual(0, state.shot_index)
        self.assertEqual(1, state.pending_index)
        battle._publish_ammo_state.assert_not_called()
        battle._sender.send_current.assert_called_once_with()

        state.commit_fire()
        self.assertEqual(1, state.shot_index)

    def test_next_shell_preselection_preserves_remaining_magazine_rounds(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        descriptor = _two_shell_descriptor()
        descriptor.gun.clip = (3, 1.0)
        state = gun_mechanics.GunState(
            descriptor, ammo_layout={101: 20, 102: 10})
        state.reload_time = 0.0
        state.clip = 2
        battle._gun_state = state
        battle._sender = types.SimpleNamespace(
            send_current=mock.Mock(return_value=True))
        battle._publish_ammo_state = mock.Mock()
        battle._publish_reload_event = mock.Mock()

        self.assertTrue(battle.change_vehicle_setting(
            runtime.constants.VEHICLE_SETTING.NEXT_SHELLS, 102))

        self.assertEqual(0, state.shot_index)
        self.assertEqual(1, state.pending_index)
        self.assertEqual(2, state.clip)
        self.assertEqual(0.0, state.reload_time)
        battle._publish_ammo_state.assert_not_called()
        battle._publish_reload_event.assert_not_called()

    def test_current_shell_setting_reloads_the_new_shell_at_once(self):
        battle, state, settings = self._shell_change_battle()
        battle.change_vehicle_setting(settings.NEXT_SHELLS, 102)

        self.assertTrue(
            battle.change_vehicle_setting(settings.CURRENT_SHELLS, 102))

        self.assertEqual(1, state.shot_index)
        self.assertIsNone(state.pending_index)
        self.assertEqual(0, state.clip)
        battle._publish_ammo_state.assert_called_once_with(state, force=True)
        battle._publish_reload_event.assert_called_once_with(
            state.reload_time, state.reload_duration, force=True)

    def test_pending_fire_commits_loaded_round_before_current_shell_switch(self):
        battle, state, settings, client, record = \
            self._pending_fire_shell_change_battle()

        self.assertTrue(battle.shoot(0.0, 0.0))
        pending = dict(battle._local_fire_intent)
        self.assertTrue(
            battle.change_vehicle_setting(settings.NEXT_SHELLS, 102))
        self.assertTrue(
            battle.change_vehicle_setting(settings.CURRENT_SHELLS, 102))

        self.assertEqual(0, state.shot_index)
        self.assertEqual(1, state.pending_index)
        self.assertEqual(1, state.clip)
        self.assertEqual(0.0, state.reload_time)
        self.assertEqual([20, 10], state.ammo)

        self.assertTrue(battle._accept_player_fire_commit({
            'shooter_kind': 'player', 'shooter_id': 1,
            'fire_intent_seq': pending['intent_seq'],
            'fire_input_seq': pending['input_seq'],
            'shot_seq': 1, 'shell_index': 0,
        }, record))

        self.assertEqual([19, 10], state.ammo)
        self.assertEqual(1, state.shot_index)
        self.assertIsNone(state.pending_index)
        self.assertEqual(0, state.clip)
        self.assertEqual(state.reload, state.reload_time)
        self.assertIsNone(battle._local_fire_intent)
        checkpoint = [message for message in client.sent
                      if message[0] == 'input'][-1][2]
        self.assertEqual(1, checkpoint['shell_index'])
        self.assertEqual(1, checkpoint['next_shell_index'])
        self.assertFalse(checkpoint['shell_change_pending'])

    def test_rejected_fire_applies_deferred_current_shell_switch(self):
        battle, state, settings, client, unused_record = \
            self._pending_fire_shell_change_battle()

        self.assertTrue(battle.shoot(0.0, 0.0))
        pending = dict(battle._local_fire_intent)
        self.assertTrue(
            battle.change_vehicle_setting(settings.NEXT_SHELLS, 102))
        self.assertTrue(
            battle.change_vehicle_setting(settings.CURRENT_SHELLS, 102))

        self.assertEqual(0, state.shot_index)
        self.assertEqual(1, state.pending_index)
        self.assertEqual(1, state.clip)
        self.assertEqual(0.0, state.reload_time)
        self.assertEqual([20, 10], state.ammo)

        self.assertTrue(battle.on_fire_intent_result({
            'type': 'fire_intent_result', 'round_id': 7,
            'player_id': 1, 'intent_seq': pending['intent_seq'],
            'accepted': False, 'reason': 'projectile_launch_rejected',
        }))

        self.assertEqual([20, 10], state.ammo)
        self.assertEqual(1, state.shot_index)
        self.assertIsNone(state.pending_index)
        self.assertEqual(0, state.clip)
        self.assertEqual(state.reload, state.reload_time)
        self.assertIsNone(battle._local_fire_intent)
        battle._avatar.cancelWaitingForShot.assert_called_once_with()
        checkpoint = [message for message in client.sent
                      if message[0] == 'input'][-1][2]
        self.assertEqual(1, checkpoint['shell_index'])
        self.assertEqual(1, checkpoint['next_shell_index'])
        self.assertFalse(checkpoint['shell_change_pending'])

    def test_pending_fire_commits_round_before_partial_clip_reload(self):
        battle, state, settings, unused_client, record = \
            self._pending_fire_shell_change_battle(clip_size=3, clip=2)

        self.assertTrue(battle.shoot(0.0, 0.0))
        pending = dict(battle._local_fire_intent)
        self.assertTrue(battle.change_vehicle_setting(
            settings.RELOAD_PARTIAL_CLIP, 0))

        self.assertEqual(2, state.clip)
        self.assertEqual(0.0, state.reload_time)
        self.assertEqual([20, 10], state.ammo)

        self.assertTrue(battle._accept_player_fire_commit({
            'shooter_kind': 'player', 'shooter_id': 1,
            'fire_intent_seq': pending['intent_seq'],
            'fire_input_seq': pending['input_seq'],
            'shot_seq': 1, 'shell_index': 0,
        }, record))

        self.assertEqual([19, 10], state.ammo)
        self.assertEqual(0, state.clip)
        self.assertEqual(state.reload, state.reload_time)
        self.assertIsNone(battle._local_fire_intent)

    def test_rejected_fire_applies_deferred_partial_clip_reload(self):
        battle, state, settings, unused_client, unused_record = \
            self._pending_fire_shell_change_battle(clip_size=3, clip=2)

        self.assertTrue(battle.shoot(0.0, 0.0))
        pending = dict(battle._local_fire_intent)
        self.assertTrue(battle.change_vehicle_setting(
            settings.RELOAD_PARTIAL_CLIP, 0))

        self.assertEqual(2, state.clip)
        self.assertEqual(0.0, state.reload_time)
        self.assertEqual([20, 10], state.ammo)

        self.assertTrue(battle.on_fire_intent_result({
            'type': 'fire_intent_result', 'round_id': 7,
            'player_id': 1, 'intent_seq': pending['intent_seq'],
            'accepted': False, 'reason': 'projectile_launch_rejected',
        }))

        self.assertEqual([20, 10], state.ammo)
        self.assertEqual(0, state.clip)
        self.assertEqual(state.reload, state.reload_time)
        self.assertIsNone(battle._local_fire_intent)
        battle._avatar.cancelWaitingForShot.assert_called_once_with()

    def test_partial_clip_setting_starts_one_full_native_reload_cycle(self):
        runtime = _runtime()
        descriptor = _two_shell_descriptor()
        descriptor.gun.clip = (3, 1.0)
        battle = BattleRuntime(runtime)
        state = gun_mechanics.GunState(
            descriptor, ammo_layout={101: 20, 102: 10})
        state.reload = 6.0
        state.clip = 2
        state.reload_time = 0.75
        state.reload_duration = 1.0
        battle._gun_state = state
        battle._publish_ammo_state = mock.Mock()
        battle._publish_reload_event = mock.Mock()

        self.assertTrue(battle.change_vehicle_setting(
            runtime.constants.VEHICLE_SETTING.RELOAD_PARTIAL_CLIP, 0))

        self.assertEqual(0, state.clip)
        self.assertEqual(6.0, state.reload_time)
        battle._publish_ammo_state.assert_called_once_with(state, force=True)
        self.assertEqual([
            mock.call(0.0, 1.0, force=True),
            mock.call(6.0, 6.0, force=True),
        ], battle._publish_reload_event.call_args_list)

    def test_partial_clip_setting_is_ignored_by_a_single_shot_gun(self):
        battle, state, settings = self._shell_change_battle()

        self.assertFalse(battle.change_vehicle_setting(
            settings.RELOAD_PARTIAL_CLIP, 0))

        self.assertEqual(1, state.clip)
        battle._publish_ammo_state.assert_not_called()
        battle._publish_reload_event.assert_not_called()

    def test_a_first_press_mid_reload_leaves_the_reload_running(self):
        battle, state, settings = self._shell_change_battle()
        state.clip = 0
        state.reload_time = 3.0

        self.assertTrue(
            battle.change_vehicle_setting(settings.NEXT_SHELLS, 102))

        self.assertEqual(0, state.shot_index)
        self.assertEqual(1, state.pending_index)
        self.assertEqual(3.0, state.reload_time)
        battle._publish_reload_event.assert_not_called()

    def test_second_press_mid_reload_restarts_the_native_hud_cycle(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        descriptor = _two_shell_descriptor()
        state = gun_mechanics.GunState(
            descriptor, ammo_layout={101: 20, 102: 10})
        state.reload = 6.0
        state.clip = 0
        state.reload_time = 3.0
        state.reload_duration = 6.0
        battle._gun_state = state
        battle._avatar = runtime.bigworld.avatar
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            send_current=mock.Mock(return_value=True))
        native_events = []
        publish_reload = battle._publish_reload_event

        def record_reload(time_left, base_time, force=False):
            native_events.append(('reload', time_left, base_time))
            return publish_reload(time_left, base_time, force=force)

        battle._publish_reload_event = record_reload
        battle._publish_ammo_state = lambda current, force=False: (
            native_events.append(('current_shell', current.shot_index)))
        settings = runtime.constants.VEHICLE_SETTING

        self.assertTrue(
            battle.change_vehicle_setting(settings.NEXT_SHELLS, 102))
        self.assertEqual([], runtime.bigworld.avatar.reload_updates)

        self.assertTrue(
            battle.change_vehicle_setting(settings.CURRENT_SHELLS, 102))

        self.assertEqual(1, state.shot_index)
        self.assertEqual(6.0, state.reload_time)
        self.assertEqual([
            (10, 0.0, 6.0),
            (10, 6.0, 6.0),
        ], runtime.bigworld.avatar.reload_updates)
        self.assertEqual([
            ('reload', 0.0, 6.0),
            ('current_shell', 1),
            ('reload', 6.0, 6.0),
        ], native_events)

        # The newly selected round completes normally and becomes fireable;
        # the HUD never needs a second shell switch to recover the gun.
        state.tick(
            6.01, True, 0.0, 0.0, 0.0, descriptor)
        self.assertEqual(0.0, state.reload_time)
        self.assertEqual(1, state.clip)
        self.assertTrue(state.can_fire(True))

    def test_loader_intuition_swaps_at_once_and_notifies_the_hud(self):
        battle, state, settings = self._shell_change_battle()
        battle._avatar = _runtime().bigworld.avatar
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._roll_loader_intuition = lambda: True

        self.assertTrue(
            battle.change_vehicle_setting(settings.CURRENT_SHELLS, 102))

        self.assertEqual(1, state.shot_index)
        self.assertEqual(0.0, state.reload_time)
        self.assertEqual(1, state.clip)
        status = battle._runtime.constants.VEHICLE_MISC_STATUS
        self.assertEqual(
            [(10, status.LOADER_INTUITION_WAS_USED, 0, (0.0,))],
            battle._avatar.misc_statuses)

    def test_two_loader_intuition_switches_leave_hud_and_ammo_consistent(self):
        battle, state, settings = self._shell_change_battle()
        battle._avatar = _runtime().bigworld.avatar
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._roll_loader_intuition = lambda: True

        self.assertTrue(
            battle.change_vehicle_setting(settings.CURRENT_SHELLS, 102))
        self.assertTrue(
            battle.change_vehicle_setting(settings.CURRENT_SHELLS, 101))

        self.assertEqual(0, state.shot_index)
        self.assertIsNone(state.pending_index)
        self.assertEqual(0.0, state.reload_time)
        self.assertEqual(1, state.clip)
        self.assertEqual(2, battle._publish_ammo_state.call_count)
        self.assertEqual(2, battle._publish_reload_event.call_count)
        self.assertEqual(2, battle._sender.send_current.call_count)
        status = battle._runtime.constants.VEHICLE_MISC_STATUS
        self.assertEqual([
            (10, status.LOADER_INTUITION_WAS_USED, 0, (0.0,)),
            (10, status.LOADER_INTUITION_WAS_USED, 0, (0.0,)),
        ], battle._avatar.misc_statuses)

    def test_loader_intuition_notification_failure_keeps_committed_shell(self):
        battle, state, settings = self._shell_change_battle()
        battle._avatar = types.SimpleNamespace(
            updateVehicleMiscStatus=mock.Mock(
                side_effect=IndexError('tuple index out of range')))
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._roll_loader_intuition = lambda: True

        self.assertTrue(
            battle.change_vehicle_setting(settings.CURRENT_SHELLS, 102))

        self.assertEqual(1, state.shot_index)
        self.assertIsNone(state.pending_index)
        self.assertEqual(0.0, state.reload_time)
        self.assertEqual(1, state.clip)
        battle._publish_ammo_state.assert_called_once_with(state, force=True)
        battle._publish_reload_event.assert_called_once_with(
            0.0, state.reload_duration, force=True)
        battle._sender.send_current.assert_called_once_with()
        battle._avatar.updateVehicleMiscStatus.assert_called_once_with(
            10,
            battle._runtime.constants.VEHICLE_MISC_STATUS.
            LOADER_INTUITION_WAS_USED,
            0, (0.0,))

    def test_an_unfinished_intuition_perk_never_rolls(self):
        battle, unused_state, unused_settings = self._shell_change_battle()
        battle._garage_loadout_snapshot = lambda: {'crew': (
            types.SimpleNamespace(skills=(types.SimpleNamespace(
                name='loader_intuition', level=42.0, isActive=True),)),)}

        self.assertFalse(battle._roll_loader_intuition())

    def test_an_unknown_shell_descriptor_is_refused(self):
        battle, state, settings = self._shell_change_battle()

        self.assertFalse(
            battle.change_vehicle_setting(settings.NEXT_SHELLS, 999))
        self.assertIsNone(state.pending_index)

    def test_siege_setting_is_sent_as_an_authoritative_input_request(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = types.SimpleNamespace(_input_seq=17)
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            send_current=mock.Mock(return_value=True))
        vehicle = types.SimpleNamespace(
            typeDescriptor=types.SimpleNamespace(hasSiegeMode=True))
        battle._server_entity = lambda unused_id: vehicle
        setting = runtime.constants.VEHICLE_SETTING.SIEGE_MODE_ENABLED

        self.assertTrue(battle.change_vehicle_setting(setting, True))
        self.assertFalse(battle.change_vehicle_setting(setting, 2))

        battle._sender.send_current.assert_called_once_with(
            siege_enabled=True)
        self.assertEqual((True, 17), battle._local_siege_pending)

    def test_siege_request_locks_drive_until_its_authoritative_echo(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle.client._input_seq = 0
        battle._avatar = runtime.bigworld.avatar
        descriptor = _Descriptor('sweden:S11_Strv_103B')
        descriptor.hasSiegeMode = True
        entity = _Vehicle(
            10, descriptor, _Vector(2, 3, 4), (0, 0, 0),
            {'health': 500})
        entity.siegeState = 0
        entity.filter.bodyMatrix = _Matrix()
        entity.filter.groundPlacingMatrix = _Matrix()
        entity.filter.groundPlacingMatrixFiltered = _Matrix()
        entity.filter.stabilisedMatrix = _Matrix()
        entity.filter.getVehiclePhysics = lambda: types.SimpleNamespace(
            setHullAimingAnglesDelta=mock.Mock())
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)

        def send_current(**unused_kwargs):
            battle.client._input_seq += 1
            return True

        battle._sender = types.SimpleNamespace(
            forward=1.0, turn=1.0, aim_yaw=0.0, gun_pitch=0.0,
            handbrake=False, send_current=mock.Mock(
                side_effect=send_current))
        battle._local_position = (2.0, 3.0, 4.0)
        battle._local_descriptor = descriptor
        battle._attach_local_presentation()

        setting = runtime.constants.VEHICLE_SETTING.SIEGE_MODE_ENABLED
        self.assertTrue(battle.change_vehicle_setting(setting, True))
        self.assertEqual((True, 1), battle._local_siege_pending)
        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'vehicle_physics.longitudinal_step') as drive, \
                mock.patch(
                    'gui.mods.offline_lan_0922.battle_runtime.'
                    'vehicle_physics.traverse_step') as traverse:
            battle._drive_local(0.1)
        drive.assert_not_called()
        traverse.assert_not_called()

        record = {
            'engine_id': 10, 'local': True,
            'presented_siege_state': 0}
        self.assertFalse(battle._apply_siege_state(record, {
            'input_seq': 0, 'siege_state': 0,
            'siege_time_left_ms': 0}))
        self.assertEqual((True, 1), battle._local_siege_pending)
        self.assertFalse(battle._apply_siege_state(record, {
            'input_seq': 1, 'siege_state': 0,
            'siege_time_left_ms': 0}))
        self.assertIsNone(battle._local_siege_pending)

    def test_rejected_siege_enqueue_preserves_the_older_pending_lock(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = types.SimpleNamespace(_input_seq=9)
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            send_current=mock.Mock(return_value=False))
        battle._server_entity = lambda unused_id: types.SimpleNamespace(
            typeDescriptor=types.SimpleNamespace(hasSiegeMode=True))
        battle._local_siege_pending = (True, 8)

        setting = runtime.constants.VEHICLE_SETTING.SIEGE_MODE_ENABLED
        self.assertFalse(battle.change_vehicle_setting(setting, False))
        self.assertEqual((True, 8), battle._local_siege_pending)

    def test_snapshot_siege_edge_drives_binding_and_active_gun_law(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        descriptor = types.SimpleNamespace(hasSiegeMode=True)
        vehicle = types.SimpleNamespace(typeDescriptor=descriptor)
        battle._server_entity = lambda unused_id: vehicle
        battle._binding = types.SimpleNamespace(
            update_vehicle_siege_state=mock.Mock(return_value=True))
        battle._gun_state = types.SimpleNamespace(
            adopt_descriptor=mock.Mock(return_value=True))
        battle._local_matrix = object()
        battle._select_local_siege_pose = mock.Mock(return_value=True)
        battle._local_physics = {'speedFwd': 19.0}
        battle._local_factors = mock.Mock(return_value={'engine/power': 1.0})
        battle._targeting_signature = ('old',)
        record = {'engine_id': 10, 'local': True}

        with mock.patch.object(
                vehicle_physics, 'derive_params',
                return_value={'speedFwd': 5.0 / 3.6}) as derive:
            self.assertTrue(battle._apply_siege_state(record, {
                'siege_state': 1, 'siege_time_left_ms': 2000}))
            self.assertFalse(battle._apply_siege_state(record, {
                'siege_state': 1, 'siege_time_left_ms': 1500}))

        battle._binding.update_vehicle_siege_state.assert_called_once_with(
            10, 1, 2.0)
        battle._select_local_siege_pose.assert_called_once_with(vehicle, False)
        battle._gun_state.adopt_descriptor.assert_called_once_with(descriptor)
        derive.assert_called_once_with(
            descriptor, {'engine/power': 1.0})
        self.assertEqual({'speedFwd': 5.0 / 3.6}, battle._local_physics)
        self.assertIsNone(battle._targeting_signature)

    def test_switching_off_keeps_the_enabled_local_hydraulic_pose(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        descriptor = types.SimpleNamespace(hasSiegeMode=True)
        vehicle = types.SimpleNamespace(typeDescriptor=descriptor)
        battle._server_entity = lambda unused_id: vehicle
        battle._binding = types.SimpleNamespace(
            update_vehicle_siege_state=mock.Mock(return_value=True))
        battle._local_matrix = object()
        battle._select_local_siege_pose = mock.Mock(return_value=True)
        record = {
            'engine_id': 10, 'local': True,
            'presented_siege_state': 2}

        self.assertTrue(battle._apply_siege_state(record, {
            'siege_state': 3, 'siege_time_left_ms': 1200}))

        battle._select_local_siege_pose.assert_called_once_with(vehicle, True)

    def test_old_siege_echo_does_not_rewrite_atomic_projectile_pose(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._binding = mock.Mock()
        record = {
            'engine_id': 11, 'local': False,
            'presented_siege_state': 1,
            'projectile_collision_pose': {
                'x': 7.0, 'y': 2.0, 'z': 9.0,
                'yaw': 0.75, 'pitch': 0.2, 'roll': -0.3,
                'turret_yaw': 0.15, 'gun_pitch': -0.1,
                'siege_state': 2,
            },
        }

        self.assertFalse(battle._apply_siege_state(record, {
            'siege_state': 1, 'siege_time_left_ms': 2000}))

        self.assertEqual(
            2, record['projectile_collision_pose']['siege_state'])
        battle._binding.update_vehicle_siege_state.assert_not_called()

    def test_record_pose_keeps_its_own_siege_state_with_old_state_echo(self):
        battle = BattleRuntime(_runtime())
        battle._binding = mock.Mock()
        record = {
            'engine_id': 11, 'local': False,
            'state': {'siege_state': 1},
        }
        pose = {
            'x': 7.0, 'y': 2.0, 'z': 9.0,
            'yaw': 0.75, 'pitch': 0.2, 'roll': -0.3,
            'aim_yaw': 0.9, 'gun_pitch': -0.1,
            'siege_state': 2,
        }

        battle._apply_record_pose(record, pose)

        self.assertEqual(
            2, record['projectile_collision_pose']['siege_state'])

    def test_player_cannot_fire_during_a_siege_transition(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._battle_live = True
        battle._battle_result = None
        battle._drown_level = 0
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._server_entity = lambda unused_id: types.SimpleNamespace(
            typeDescriptor=types.SimpleNamespace(), siegeState=1)

        self.assertFalse(battle.shoot(0.0, 0.0))

    def test_bot_observation_drives_only_enemy_visibility_feedback(self):
        battle = BattleRuntime(_runtime())
        observed = []
        battle.client = types.SimpleNamespace(player_id=7, team=1)
        battle._sixth_sense = types.SimpleNamespace(
            observe=lambda visible, now: observed.append((visible, now)))
        message = {'type': 'bot_observation', 'contacts': [
            {'target_kind': 'human', 'target_id': 7,
             'observing_team': 1, 'visible': True, 'fresh': True},
            {'target_kind': 'human', 'target_id': 7,
             'observing_team': 2, 'visible': True, 'fresh': True},
        ]}

        self.assertTrue(battle._observe_local_vehicle(message, 12.5))
        self.assertEqual([(True, 12.5)], observed)

    def test_relayed_observation_reaches_authority_and_guest_once(self):
        def client_runtime(player_id):
            battle = BattleRuntime(_runtime())
            battle.state = 'running'
            battle._start_message = {'round_id': 7}
            battle.client = types.SimpleNamespace(player_id=player_id, team=1)
            battle._clock = mock.Mock(return_value=12.5)
            observed = []
            battle._sixth_sense = types.SimpleNamespace(
                observe=lambda visible, now: observed.append((visible, now)))
            return battle, observed

        authority, authority_observed = client_runtime(1)
        guest, guest_observed = client_runtime(2)
        authority_target = {
            'type': 'bot_observation', 'round_id': 7,
            'contacts': [{
                'target_kind': 'human', 'target_id': 1,
                'target_team': 1, 'observing_team': 2,
                'visible': True, 'fresh': True, 'time_left': 10.0,
                'visible_by_bot_ids': [11],
                'visible_by_player_ids': [],
                'shootable_by_bot_ids': [],
            }],
        }

        self.assertTrue(authority.on_bot_observation(authority_target))
        self.assertFalse(guest.on_bot_observation(authority_target))
        self.assertEqual([(True, 12.5)], authority_observed)
        self.assertEqual([(False, 12.5)], guest_observed)

        guest_target = dict(authority_target)
        guest_target['contacts'] = [dict(
            authority_target['contacts'][0], target_id=2)]
        self.assertTrue(guest.on_bot_observation(guest_target))
        self.assertEqual((True, 12.5), guest_observed[-1])

        hidden = dict(guest_target)
        hidden['contacts'] = [dict(
            guest_target['contacts'][0], visible=False, fresh=False,
            time_left=0.0, visible_by_bot_ids=[])]
        self.assertFalse(guest.on_bot_observation(hidden))
        self.assertEqual(1, sum(1 for visible, unused_now in guest_observed
                                if visible))

        stale = dict(guest_target, round_id=6)
        self.assertFalse(guest.on_bot_observation(stale))
        self.assertEqual(3, len(guest_observed))

    def test_bigworld_entity_rotation_keeps_yaw_out_of_roll(self):
        prohorovka_team_one_yaw = 2.947

        self.assertEqual(
            (0.0, 0.0, prohorovka_team_one_yaw),
            _engine_rotation(prohorovka_team_one_yaw))
        self.assertEqual(
            (-0.25, 0.125, prohorovka_team_one_yaw),
            _engine_rotation(prohorovka_team_one_yaw, 0.125, -0.25))

    def test_standard_arena_matches_space_prefixed_geometry_name(self):
        runtime = _runtime()
        arena = types.SimpleNamespace(
            geometryName='spaces/31_airfield', gameplayName='ctf')
        runtime.arena_cache = {7: arena}
        battle = BattleRuntime(runtime)

        self.assertIs(arena, battle._standard_arena('31_airfield'))

    def test_player_arena_boundary_uses_official_box_and_chassis_corners(self):
        battle = BattleRuntime(_runtime())
        arena = types.SimpleNamespace(boundingBox=(
            _Vector(-300.0, -300.0), _Vector(300.0, 300.0)))
        battle._arena_bounds = battle._arena_bounds_from_type(arena)
        entity = _Vehicle(
            10, _Descriptor(), _Vector(), (0, 0, 0), {'health': 500})
        position = (296.49, 7.0, -20.0)

        self.assertEqual(
            (-300.0, -300.0, 300.0, 300.0), battle._arena_bounds)
        # At pi/2 the 3.5 m chassis half-length reaches the east edge.
        self.assertFalse(battle._arena_motion_is_clear(
            entity, position, math.pi * 0.5, 1.0, 0.1))
        self.assertTrue(battle._arena_motion_is_clear(
            entity, position, 0.0, 1.0, 0.1,
            hull_yaw=math.pi * 0.5))
        self.assertFalse(battle._arena_rotation_is_clear(
            entity, position, math.pi * 0.5, 1.2))

        # An already stale pose can move or drift inward without a teleport,
        # but it cannot increase its overflow past the same official edge.
        outside = (301.0, 100.0, -20.0)
        self.assertTrue(battle._arena_motion_is_clear(
            entity, outside, -math.pi * 0.5, 1.0, 0.1,
            hull_yaw=math.pi * 0.5))
        self.assertFalse(battle._arena_motion_is_clear(
            entity, outside, math.pi * 0.5, 1.0, 0.1,
            hull_yaw=math.pi * 0.5))
        self.assertTrue(battle._arena_rotation_is_clear(
            entity, outside, math.pi * 0.5, 0.0))

    def test_player_arena_boundary_blocks_before_native_world_probe(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._arena_bounds = (-300.0, -300.0, 300.0, 300.0)
        entity = _Vehicle(
            10, _Descriptor(), _Vector(), (0, 0, 0), {'health': 500})

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'world_collision.check_horizontal_collision',
                return_value='clear') as world_probe:
            self.assertFalse(battle._motion_is_clear(
                entity, (296.49, 7.0, 0.0), math.pi * 0.5,
                1.0, 0.1))
            self.assertEqual('hard', battle._local_motion_status)
            self.assertEqual('arena', battle._local_motion_kinds)
            world_probe.assert_not_called()

            self.assertTrue(battle._motion_is_clear(
                entity, (296.49, 7.0, 0.0), 0.0, 1.0, 0.1,
                hull_yaw=math.pi * 0.5))
            world_probe.assert_called_once()

    def test_supported_map_installs_catalog_before_native_destructible_reset(self):
        runtime = _runtime()
        runtime.area_destructibles = object()
        runtime.destructibles_cache = object()
        catalog = {'map': '01_karelia', 'resources': {'proved': {}}}
        runtime.destructible_catalog_loader = mock.Mock(
            return_value=catalog)
        battle = BattleRuntime(runtime)
        module = sys.modules[BattleRuntime.__module__]
        from gui.mods.offline_lan_0922 import destructibles_sensor
        calls = []

        with mock.patch.object(
                module.destructibles_compat, 'install', return_value=True), \
                mock.patch.object(
                    destructibles_sensor, 'set_catalog',
                    side_effect=lambda value: calls.append(
                        ('catalog', value))), \
                mock.patch.object(
                    destructibles_sensor, 'reset',
                    side_effect=lambda space_id=None: calls.append(
                        ('reset', space_id))), \
                mock.patch.object(
                    destructibles_sensor, 'set_event_sink',
                    side_effect=lambda sink: calls.append(
                        ('sink', sink))):
            self.assertTrue(battle.start({
                'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
                'name': 'Player'}, _minimal_start(), _Client()))
            runtime.destructible_catalog_loader.assert_called_once_with(
                '01_karelia')
            self.assertIs(catalog, calls[0][1])
            self.assertEqual('catalog', calls[0][0])
            self.assertEqual(('reset', 7), calls[1])
            self.assertEqual('sink', calls[2][0])

            battle.stop(show_login=False)

        self.assertEqual(('catalog', None), calls[-1])

    def test_battle_start_replays_canonical_destructible_ledger_after_reset(self):
        runtime = _runtime()
        runtime.area_destructibles = object()
        runtime.destructibles_cache = object()
        runtime.destructible_catalog_loader = mock.Mock(return_value={
            'map': '01_karelia', 'resources': {}})
        battle = BattleRuntime(runtime)
        module = sys.modules[BattleRuntime.__module__]
        from gui.mods.offline_lan_0922 import destructibles_sensor
        calls = []
        ledger = [{
            'destructible_kind': 'tree',
            'chunk_id': 3, 'item_index': 9,
            'x': 1.0, 'y': 2.0, 'z': 3.0,
            'fall_yaw': 0.75, 'speed': 2.0,
            'is_shot': False,
        }]
        message = _minimal_start()
        message['destructibles'] = ledger

        def apply_state(events):
            calls.append(('ledger', events))
            return True

        battle._apply_destructible_state = mock.Mock(side_effect=apply_state)
        with mock.patch.object(
                module.destructibles_compat, 'install', return_value=True), \
                mock.patch.object(destructibles_sensor, 'set_catalog'), \
                mock.patch.object(
                    destructibles_sensor, 'reset',
                    side_effect=lambda space_id=None: calls.append(
                        ('reset', space_id))), \
                mock.patch.object(
                    destructibles_sensor, 'set_event_sink',
                    side_effect=lambda sink: calls.append(('sink', sink))):
            self.assertTrue(battle.start({
                'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
                'name': 'Player'}, message, _Client()))
            battle.stop(show_login=False)

        self.assertEqual('reset', calls[0][0])
        self.assertEqual('sink', calls[1][0])
        self.assertEqual(('ledger', ledger), calls[2])
        battle._apply_destructible_state.assert_called_once_with(ledger)

    def test_destructible_catalog_failure_disables_only_that_feature(self):
        module = sys.modules[BattleRuntime.__module__]
        from gui.mods.offline_lan_0922 import destructibles_sensor

        for loader_effect in (None, RuntimeError('catalog read failed')):
            with self.subTest(loader_effect=loader_effect):
                runtime = _runtime()
                runtime.area_destructibles = object()
                runtime.destructibles_cache = object()
                if isinstance(loader_effect, Exception):
                    runtime.destructible_catalog_loader = mock.Mock(
                        side_effect=loader_effect)
                else:
                    runtime.destructible_catalog_loader = mock.Mock(
                        return_value=loader_effect)
                battle = BattleRuntime(runtime)

                with mock.patch.object(
                        module.destructibles_compat, 'install',
                        return_value=True), mock.patch.object(
                            destructibles_sensor, 'set_catalog'), \
                        mock.patch.object(destructibles_sensor, 'reset'), \
                        mock.patch.object(
                            destructibles_sensor, 'set_event_sink'), \
                        contextlib.redirect_stdout(io.StringIO()) as log:
                    self.assertTrue(battle.start({
                        'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
                        'name': 'Player'}, {}, _Client()))
                    self.assertIsNone(battle._destructibles)
                    battle.stop(show_login=False)

                self.assertIn(
                    'optional destructible interactions disabled for this '
                    'round', log.getvalue())
                self.assertIn(
                    ('map_create', '01_karelia'),
                    runtime.bigworld.operations)

    def test_foliage_loader_failure_does_not_abort_map_start(self):
        runtime = _runtime()
        runtime.foliage_loader = mock.Mock(
            side_effect=RuntimeError('foliage file is incomplete'))
        battle = BattleRuntime(runtime)

        with contextlib.redirect_stdout(io.StringIO()) as log:
            self.assertTrue(battle.start({
                'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
                'name': 'Player'}, {}, _Client()))
            self.assertIsNone(battle._foliage)
            battle.stop(show_login=False)

        self.assertEqual(
            1, log.getvalue().count(
                'optional foliage camouflage disabled for this round'))

    def test_baked_formation_slot_is_reused_without_runtime_nudging(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._config = {'map': '31_airfield'}
        battle._navigation_graph = runtime.navigation_graph_loader(
            '31_airfield')

        first = battle._formation_pose(1, 3)
        second = battle._formation_pose(1, 3)

        self.assertEqual(first, second)
        self.assertEqual(((12.0, 0.0, -80.0), 0.0), first)

    def test_missing_baked_formation_fails_instead_of_searching_locally(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._config = {'map': '01_karelia'}
        battle._navigation_graph = {'map': '01_karelia'}

        with self.assertRaisesRegex(ValueError, 'spawn formations are missing'):
            battle._formation_pose(1, 0)

    def test_bot_roster_uses_complete_catalog_without_persistent_pool(self):
        runtime = _runtime()
        runtime.nations = types.SimpleNamespace(
            AVAILABLE_NAMES=('ussr',), INDICES={'ussr': 0})
        entries = {
            1: types.SimpleNamespace(
                level=8, tags=frozenset(('heavyTank',)),
                name='ussr:heavy'),
            2: types.SimpleNamespace(
                level=8, tags=frozenset(('mediumTank',)),
                name='ussr:medium'),
        }
        runtime.vehicles.g_list = types.SimpleNamespace(
            getList=lambda unused_nation_id: entries)
        descriptor = _Descriptor('china:Ch22_113P')
        descriptor.type.level = 8
        battle = BattleRuntime(runtime)
        battle._config = {'vehicle': descriptor.name}
        battle._start_message = {'players': [
            {'id': 1, 'team': 1, 'vehicle': descriptor.name},
        ], 'bots': [
            {'team': 1, 'slot': 0}, {'team': 1, 'slot': 1},
            {'team': 2, 'slot': 0}, {'team': 2, 'slot': 1},
        ]}
        battle.client = types.SimpleNamespace(team=1, player_id=1)

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.random.random',
                side_effect=(0.0, 0.0)), \
                mock.patch(
                    'gui.mods.offline_lan_0922.battle_runtime.random.shuffle',
                    side_effect=lambda values: None):
            self.assertTrue(
                battle._prepare_bot_vehicle_assignments(descriptor))

        assignments = battle._bot_vehicle_assignments
        self.assertEqual(4, len(assignments))
        self.assertIn('ussr:heavy', assignments.values())
        self.assertIn('ussr:medium', assignments.values())
        self.assertIn(descriptor.name, assignments.values())
        self.assertNotIn('_BOT_POOL_BY_TIER', vars(sys.modules[
            'gui.mods.offline_lan_0922.battle_runtime']))

    def test_all_clients_preload_the_same_server_roster(self):
        runtime = _runtime()
        runtime.nations = types.SimpleNamespace(
            AVAILABLE_NAMES=('ussr',), INDICES={'ussr': 0})
        entries = {
            1: types.SimpleNamespace(
                level=8, tags=frozenset(('heavyTank',)),
                name='ussr:heavy'),
            2: types.SimpleNamespace(
                level=8, tags=frozenset(('mediumTank',)),
                name='ussr:medium'),
            3: types.SimpleNamespace(
                level=8, tags=frozenset(('AT-SPG',)),
                name='ussr:td'),
        }
        runtime.vehicles.g_list = types.SimpleNamespace(
            getList=lambda unused_nation_id: entries)
        first_descriptor = _Descriptor('ussr:first')
        first_descriptor.type.level = 8
        second_descriptor = _Descriptor('ussr:second')
        second_descriptor.type.level = 8
        descriptors = {
            first_descriptor.name: first_descriptor,
            second_descriptor.name: second_descriptor,
        }
        start = {
            'round_id': 17, 'map': '02_malinovka',
            'players': [
                {'id': 1, 'team': 1, 'slot': 0,
                 'vehicle': first_descriptor.name},
                {'id': 2, 'team': 2, 'slot': 0,
                 'vehicle': second_descriptor.name},
            ],
            'bots': [
                {'id': 11, 'team': 1, 'slot': 1},
                {'id': 12, 'team': 1, 'slot': 2},
                {'id': 21, 'team': 2, 'slot': 1},
                {'id': 22, 'team': 2, 'slot': 2},
            ],
        }

        def make_battle(player_id):
            battle = BattleRuntime(runtime)
            battle._config = {'vehicle': descriptors[
                first_descriptor.name if player_id == 1 else
                second_descriptor.name].name}
            battle._start_message = start
            battle.client = types.SimpleNamespace(
                team=player_id, player_id=player_id)
            battle._resolve_descriptor = lambda name: descriptors[name]
            return battle

        first = make_battle(1)
        second = make_battle(2)
        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.random.random',
                side_effect=AssertionError('global RNG used')), \
                mock.patch(
                    'gui.mods.offline_lan_0922.battle_runtime.random.shuffle',
                    side_effect=AssertionError('global RNG used')):
            self.assertTrue(first._prepare_bot_vehicle_assignments(
                first_descriptor))
            self.assertTrue(second._prepare_bot_vehicle_assignments(
                second_descriptor))

        self.assertEqual(
            first._bot_vehicle_assignments,
            second._bot_vehicle_assignments)

    def test_game_abort_is_rejected_and_original_is_restored(self):
        runtime = _runtime()
        original_abort = runtime.game.abort
        normal_create = runtime.offline_map_creator.create

        def create_then_abort(unused_map_name):
            runtime.game.abort()

        runtime.offline_map_creator.create = create_then_abort
        battle = BattleRuntime(runtime)

        self.assertFalse(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, _minimal_start(3), _Client()))

        self.assertIs(original_abort, runtime.game.abort)
        original_abort.assert_not_called()
        self.assertEqual('failed', battle.state)
        self.assertIn('game.abort', battle.error)

        runtime.bigworld.callbacks.pop()()
        runtime.offline_map_creator.create = normal_create
        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, _minimal_start(4), _Client()))

    def test_game_abort_patch_does_not_overwrite_a_newer_patch(self):
        runtime = _runtime()

        def newer_abort():
            return 'newer'

        def replace_during_create(unused_map_name):
            runtime.game.abort = newer_abort

        runtime.offline_map_creator.create = replace_during_create
        battle = BattleRuntime(runtime)

        self.assertFalse(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, _minimal_start(3), _Client()))

        self.assertIs(newer_abort, runtime.game.abort)
        self.assertEqual('newer', runtime.game.abort())

    def test_lobby_is_retired_before_native_map_without_viewer_camera(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()
        start = {
            'round_id': 1, 'map': '01_karelia', 'bot_authority_id': 1,
            'players': [{
                'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                'vehicle': 'ussr:R11_MS-1', 'health': 500}],
            'bots': []}

        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, start, client))

        self.assertEqual(
            [('account_retire',), ('hangar_destroy',),
             ('clear_entities_spaces',),
             ('watcher', 'Visibility/GUI', False),
             ('map_create', '01_karelia'),
             ('watcher', 'Visibility/GUI', True),
             ('space_visibility', 7, 0x00000001)],
            runtime.bigworld.operations)
        self.assertEqual([0x00000001],
                         runtime.bigworld.mapped_visibility_masks)
        self.assertEqual([], runtime.bigworld.legacy_visibility_calls)
        self.assertNotIn(
            'addSpaceGeometryMapping', runtime.bigworld.__dict__)
        self.assertEqual(0, runtime.offline_map_creator.viewer_camera_calls)
        self.assertEqual([(4, 5)], runtime.app_loader.transitions)
        self.assertEqual(1, runtime.app_loader.lobby_disposals)
        self.assertEqual(1, runtime.app_loader.lobby_populates)
        self.assertEqual(0, runtime.app_loader.lobby_listener_balance)
        self.assertEqual(
            1, runtime.bigworld.spaces[7].itemsVisibilityMask)
        self.assertFalse(hasattr(runtime.app_loader, '__dict__'))
        type(runtime.app_loader).battle_page_calls.assert_not_called()
        self.assertFalse(runtime.offline_map_creator.Active())
        self.assertTrue(runtime.bigworld.avatar._offlineLANPlayerReady)

        runtime.app_loader.showBattlePage()
        type(runtime.app_loader).battle_page_calls.assert_called_once_with()

    def test_malinovka_mapping_filters_non_ctf_control_points(self):
        class _UnsignedMask(int):
            def __int__(self):
                raise OverflowError('32-bit Python cannot narrow this mask')

        runtime = _runtime()
        runtime.client_visibility_flags.CLIENT_MASK = _UnsignedMask(
            0xfff00000)
        runtime.client_visibility_flags.SERVER_MASK = _UnsignedMask(
            0x000fffff)
        runtime.arena_cache = {2: types.SimpleNamespace(
            geometryName='02_malinovka', gameplayName='ctf', gameplayID=0)}
        battle = BattleRuntime(runtime)
        start = {
            'round_id': 1, 'map': '02_malinovka', 'bot_authority_id': 1,
            'players': [{
                'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                'vehicle': 'ussr:R11_MS-1', 'health': 500}],
            'bots': []}

        self.assertTrue(battle.start({
            'map': '02_malinovka', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, start, _Client()))

        self.assertEqual(
            0x00000001,
            runtime.bigworld.spaces[7].itemsVisibilityMask)
        self.assertEqual([0x00000001],
                         runtime.bigworld.mapped_visibility_masks)
        self.assertEqual([], runtime.bigworld.legacy_visibility_calls)

        # Exact #1513 Malinovka WTCP records: both CTF bases include bit 0;
        # the neutral domination and base-3 records do not.  The mapping is
        # constructed with bit 0, so native WTCP instantiates only CTF bases.
        self.assertEqual(
            [[0xffffff89, 0xffffff89]],
            runtime.bigworld.mapped_control_point_masks)

    def test_native_map_does_not_use_inert_legacy_visibility_setter(self):
        runtime = _runtime()
        original_set_mask = runtime.bigworld.wg_setSpaceItemsVisibilityMask
        live_space_at_write = []

        def set_mask(space_id, mask):
            live_space_at_write.append(int(space_id) in runtime.bigworld.spaces)
            return original_set_mask(space_id, mask)

        runtime.bigworld.wg_setSpaceItemsVisibilityMask = mock.Mock(
            side_effect=set_mask)
        battle = BattleRuntime(runtime)

        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, {
                'round_id': 1, 'map': '01_karelia',
                'bot_authority_id': 1,
                'players': [{
                    'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                    'vehicle': 'ussr:R11_MS-1', 'health': 500}],
                'bots': []}, _Client()))

        self.assertEqual(
            1, runtime.bigworld.spaces[7].itemsVisibilityMask)
        self.assertEqual([0x00000001],
                         runtime.bigworld.mapped_visibility_masks)
        self.assertEqual([], live_space_at_write)
        runtime.bigworld.wg_setSpaceItemsVisibilityMask.assert_not_called()
        self.assertEqual([], runtime.bigworld.legacy_visibility_calls)
        self.assertEqual([(4, 5)], runtime.app_loader.transitions)
        self.assertEqual(0, runtime.app_loader.lobby_listener_balance)

    def test_native_map_restores_gui_visibility_after_mapping(self):
        runtime = _runtime()
        runtime.arena_cache = {2: types.SimpleNamespace(
            geometryName='02_malinovka', gameplayName='ctf', gameplayID=0)}
        battle = BattleRuntime(runtime)

        self.assertTrue(battle.start({
            'map': '02_malinovka', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, {
                'round_id': 1, 'map': '02_malinovka',
                'bot_authority_id': 1,
                'players': [{
                    'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                    'vehicle': 'ussr:R11_MS-1', 'health': 500}],
                'bots': []}, _Client()))

        self.assertEqual([False], runtime.bigworld.mapped_gui_visibility)
        self.assertTrue(runtime.bigworld.gui_visibility)
        self.assertEqual([0x00000001],
                         runtime.bigworld.mapped_visibility_masks)
        self.assertLess(
            runtime.bigworld.operations.index(
                ('watcher', 'Visibility/GUI', False)),
            runtime.bigworld.operations.index(
                ('map_create', '02_malinovka')))
        self.assertLess(
            runtime.bigworld.operations.index(
                ('map_create', '02_malinovka')),
            runtime.bigworld.operations.index(
                ('watcher', 'Visibility/GUI', True)))

    def test_native_map_does_not_require_live_space_before_mapping(self):
        runtime = _runtime()
        original_add_mapping = runtime.bigworld.addSpaceGeometryMapping
        live_space_at_mapping = []

        def add_mapping(space_id, mapper, path):
            live_space_at_mapping.append(
                int(space_id) in runtime.bigworld.spaces)
            return original_add_mapping(space_id, mapper, path)

        runtime.bigworld.addSpaceGeometryMapping = add_mapping
        battle = BattleRuntime(runtime)

        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, {
                'round_id': 1, 'map': '01_karelia',
                'bot_authority_id': 1,
                'players': [{
                    'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                    'vehicle': 'ussr:R11_MS-1', 'health': 500}],
                'bots': []}, _Client()))

        self.assertEqual([False], live_space_at_mapping)
        self.assertEqual([0x00000001],
                         runtime.bigworld.mapped_visibility_masks)
        self.assertIs(
            add_mapping,
            runtime.bigworld.__dict__['addSpaceGeometryMapping'])

    def test_visibility_retries_when_live_space_publication_lags_mapping(self):
        runtime = _runtime()
        runtime.arena_cache = {2: types.SimpleNamespace(
            geometryName='02_malinovka', gameplayName='ctf', gameplayID=0)}
        original_add_mapping = runtime.bigworld.addSpaceGeometryMapping
        delayed_spaces = []

        def map_before_publishing_space(space_id, mapper, path):
            result = original_add_mapping(space_id, mapper, path)
            delayed_spaces.append(runtime.bigworld.spaces.pop(int(space_id)))
            return result

        runtime.bigworld.addSpaceGeometryMapping = map_before_publishing_space
        battle = BattleRuntime(runtime)
        start = {
            'round_id': 1, 'map': '02_malinovka', 'bot_authority_id': 1,
            'players': [{
                'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                'vehicle': 'ussr:R11_MS-1', 'health': 500}],
            'bots': []}

        self.assertTrue(battle.start({
            'map': '02_malinovka', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, start, _Client()))

        self.assertEqual([0x00000001],
                         runtime.bigworld.mapped_visibility_masks)
        self.assertIsNotNone(battle._standard_space_visibility)
        self.assertFalse(battle._space_visibility_warning_reported)

        runtime.bigworld.now += \
            battle_runtime_module.SPACE_VISIBILITY_CHECK_SECONDS
        self.assertFalse(battle._maintain_standard_space_visibility(
            runtime.bigworld.now))
        self.assertIsNotNone(battle._standard_space_visibility)
        self.assertFalse(battle._space_visibility_warning_reported)

        # PySpaces becomes visible on a later engine tick, after another
        # stock update has widened the server mask to include domination.
        delayed_space = delayed_spaces[0]
        delayed_space._items_visibility_mask = 0x00000003
        runtime.bigworld.spaces[7] = delayed_space
        runtime.bigworld.now += \
            battle_runtime_module.SPACE_VISIBILITY_CHECK_SECONDS

        self.assertTrue(battle._maintain_standard_space_visibility(
            runtime.bigworld.now))
        self.assertEqual(0x00000001,
                         runtime.bigworld.spaces[7].itemsVisibilityMask)
        self.assertFalse(0xffffff82 &
                         runtime.bigworld.spaces[7].itemsVisibilityMask)
        self.assertFalse(battle._space_visibility_warning_reported)

    def test_incomplete_hangar_fails_before_native_clear(self):
        runtime = _runtime()
        hangar = runtime.hangar_space.g_hangarSpace
        hangar.spaceInited = False
        runtime.offline_map_creator.create = mock.Mock()
        battle = BattleRuntime(runtime)

        self.assertFalse(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, _minimal_start(), _Client()))

        self.assertEqual([], runtime.bigworld.operations)
        runtime.offline_map_creator.create.assert_not_called()
        self.assertEqual('failed', battle.state)
        self.assertIn('hangar space is not ready', battle.error)

    def test_incomplete_hangar_destroy_fails_before_native_clear(self):
        runtime = _runtime()
        hangar = runtime.hangar_space.g_hangarSpace

        def incomplete_destroy():
            runtime.bigworld.operations.append(('hangar_destroy',))
            hangar.inited = False

        hangar.destroy = incomplete_destroy
        runtime.offline_map_creator.create = mock.Mock()
        battle = BattleRuntime(runtime)

        self.assertFalse(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, _minimal_start(), _Client()))

        self.assertEqual(
            [('account_retire',), ('hangar_destroy',),
             ('clear_entities_spaces',)],
            runtime.bigworld.operations)
        runtime.offline_map_creator.create.assert_not_called()
        self.assertEqual('failed', battle.state)
        self.assertIn(
            'Account retirement did not destroy the hangar space',
            battle.error)

    def test_failed_lobby_clear_uses_second_boundary_before_restore(self):
        runtime = _runtime()

        def failing_clear():
            runtime.bigworld.operations.append(('clear_failed',))
            raise RuntimeError('first clear failed')

        def fallback_clear():
            runtime.bigworld.operations.append(('clear_all_spaces',))
            runtime.bigworld.avatar = None

        runtime.bigworld.clearEntitiesAndSpaces = failing_clear
        runtime.bigworld.clearAllSpaces = fallback_clear
        battle = BattleRuntime(runtime)

        self.assertFalse(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, _minimal_start(), _Client()))

        self.assertEqual([
            ('account_retire',), ('hangar_destroy',), ('clear_failed',),
            ('clear_failed',), ('clear_all_spaces',),
            ('offline_disconnect',),
        ], runtime.bigworld.operations)
        self.assertFalse(getattr(
            runtime.compatibility, 'account_restored', False))
        self.assertEqual(1, runtime.compatibility.disconnect_calls)
        self.assertEqual('failed', battle.state)
        self.assertIn('first clear failed', battle.error)

    def test_retained_lobby_account_is_forced_out_before_restore(self):
        runtime = _runtime()

        def retaining_clear():
            runtime.bigworld.operations.append(('clear_retained',))

        def fallback_clear():
            runtime.bigworld.operations.append(('clear_all_spaces',))
            runtime.bigworld.avatar = None

        runtime.bigworld.clearEntitiesAndSpaces = retaining_clear
        runtime.bigworld.clearAllSpaces = fallback_clear
        battle = BattleRuntime(runtime)

        self.assertFalse(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, _minimal_start(), _Client()))

        runtime.bigworld.callbacks.pop()()
        self.assertEqual([
            ('account_retire',), ('hangar_destroy',), ('clear_retained',),
            ('clear_retained',), ('clear_all_spaces',),
        ], runtime.bigworld.operations)
        self.assertTrue(runtime.compatibility.account_restored)
        self.assertEqual('failed', battle.state)
        self.assertIn('lobby Account survived', battle.error)

    def test_missing_viewer_camera_boundary_fails_closed_and_restores_lobby(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        calls = []
        runtime.offline_map_creator._OfflineMapCreator__setupCamera = None
        runtime.offline_map_creator.create = mock.Mock()

        def destroy():
            calls.append('destroy')
            runtime.bigworld.avatar = None

        def restore():
            self.assertIsNone(runtime.bigworld.avatar)
            calls.append('restore')

        runtime.offline_map_creator.destroy = destroy
        runtime.compatibility.restore_lobby_account = restore

        self.assertFalse(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, _minimal_start(), _Client()))

        runtime.bigworld.callbacks.pop()()
        runtime.offline_map_creator.create.assert_not_called()
        self.assertEqual(['destroy', 'restore'], calls)
        self.assertEqual('failed', battle.state)
        self.assertFalse(battle._map_create_attempted)

    def test_missing_battle_page_boundary_preserves_existing_lobby(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        account = runtime.bigworld.avatar
        type(runtime.app_loader).showBattlePage = None
        runtime.offline_map_creator.create = mock.Mock()

        self.assertFalse(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, _minimal_start(), _Client()))

        runtime.bigworld.callbacks.pop()()
        runtime.offline_map_creator.create.assert_not_called()
        self.assertIs(account, runtime.bigworld.avatar)
        self.assertEqual([], runtime.bigworld.operations)
        self.assertEqual([], runtime.app_loader.transitions)
        self.assertEqual(1, runtime.app_loader.lobby_listener_balance)
        self.assertTrue(runtime.compatibility.account_restored)
        self.assertEqual('failed', battle.state)
        self.assertFalse(battle._map_create_attempted)

    def test_rejected_loading_preserves_lobby_and_next_round_can_start(self):
        runtime = _runtime()
        account = runtime.bigworld.avatar
        runtime.offline_map_creator.create = mock.Mock(
            wraps=runtime.offline_map_creator.create)
        type(runtime.app_loader).battle_loading_calls.return_value = False
        battle = BattleRuntime(runtime)

        self.assertFalse(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, _minimal_start(), _Client()))

        self.assertIs(account, runtime.bigworld.avatar)
        self.assertEqual([], runtime.bigworld.operations)
        self.assertEqual([(4, 5)], runtime.app_loader.transitions)
        self.assertEqual(4, runtime.app_loader.actual_space_id)
        self.assertEqual(0, runtime.app_loader.lobby_disposals)
        self.assertEqual(1, runtime.app_loader.lobby_populates)
        self.assertEqual(1, runtime.app_loader.lobby_listener_balance)
        runtime.offline_map_creator.create.assert_not_called()

        runtime.bigworld.callbacks.pop()()
        type(runtime.app_loader).battle_loading_calls.return_value = True
        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, _minimal_start(2), _Client()))

        self.assertEqual([(4, 5), (4, 5)], runtime.app_loader.transitions)
        self.assertEqual(1, runtime.app_loader.lobby_disposals)
        self.assertEqual(0, runtime.app_loader.lobby_listener_balance)

    def test_inert_legacy_visibility_setter_is_never_consulted(self):
        runtime = _runtime()
        write_count = [0]
        original_set_mask = runtime.bigworld.wg_setSpaceItemsVisibilityMask

        def reject_first_write(space_id, mask):
            write_count[0] += 1
            if write_count[0] == 1:
                runtime.bigworld.operations.append(
                    ('space_visibility_rejected', int(space_id), mask))
                raise ValueError('native visibility write rejected')
            return original_set_mask(space_id, mask)

        runtime.bigworld.wg_setSpaceItemsVisibilityMask = reject_first_write
        battle = BattleRuntime(runtime)
        start = {
            'round_id': 1, 'map': '01_karelia', 'bot_authority_id': 1,
            'players': [{
                'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                'vehicle': 'ussr:R11_MS-1', 'health': 500}],
            'bots': []}

        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, start, _Client()))

        self.assertIsNone(battle.error)
        self.assertEqual([(4, 5)], runtime.app_loader.transitions)
        self.assertEqual(1, runtime.app_loader.lobby_disposals)
        self.assertEqual(1, runtime.app_loader.lobby_populates)
        self.assertEqual(0, runtime.app_loader.lobby_listener_balance)
        self.assertEqual([0x00000001],
                         runtime.bigworld.mapped_visibility_masks)
        self.assertEqual(
            1, runtime.bigworld.spaces[7].itemsVisibilityMask)
        self.assertEqual(0, write_count[0])
        self.assertFalse(battle._space_visibility_warning_reported)

    def test_legacy_zero_readback_does_not_abort_mapped_battle(self):
        runtime = _runtime()
        original_create = runtime.offline_map_creator.create

        def create_then_remove_legacy_setter(map_name):
            original_create(map_name)
            runtime.bigworld.wg_setSpaceItemsVisibilityMask = None

        runtime.offline_map_creator.create = \
            create_then_remove_legacy_setter
        battle = BattleRuntime(runtime)
        start = {
            'round_id': 1, 'map': '01_karelia', 'bot_authority_id': 1,
            'players': [{
                'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                'vehicle': 'ussr:R11_MS-1', 'health': 500}],
            'bots': []}

        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, start, _Client()))

        # Reproduce the exact VM symptom without consulting it during start:
        # the legacy API still reports zero, while the mapped typed property
        # contains the selected gameplay mask and the battle remains live.
        self.assertEqual(0,
                         runtime.bigworld.wg_getSpaceItemsVisibilityMask(7))
        self.assertEqual(1,
                         runtime.bigworld.spaces[7].itemsVisibilityMask)
        self.assertIsNone(battle.error)
        self.assertFalse(battle._space_visibility_warning_reported)
        self.assertIn(
            ('get', 7),
            runtime.bigworld.legacy_visibility_calls)

    def test_post_mapping_zero_mask_is_repaired_only_through_typed_space(self):
        runtime = _runtime()
        original_create = runtime.offline_map_creator.create

        def create_then_clear_typed_mask(map_name):
            original_create(map_name)
            runtime.bigworld.spaces[7]._items_visibility_mask = 0

        runtime.offline_map_creator.create = create_then_clear_typed_mask
        battle = BattleRuntime(runtime)
        start = {
            'round_id': 1, 'map': '01_karelia', 'bot_authority_id': 1,
            'players': [{
                'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                'vehicle': 'ussr:R11_MS-1', 'health': 500}],
            'bots': []}

        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, start, _Client()))

        self.assertEqual(1,
                         runtime.bigworld.spaces[7].itemsVisibilityMask)
        self.assertEqual([], runtime.bigworld.legacy_visibility_calls)
        self.assertFalse(battle._space_visibility_warning_reported)

    def test_typed_visibility_write_failure_does_not_abort_battle(self):
        class _RejectTypedSpaceData(_SpaceData):
            @property
            def itemsVisibilityMask(self):
                return self._items_visibility_mask

            @itemsVisibilityMask.setter
            def itemsVisibilityMask(self, mask):
                self._operations.append(
                    ('space_visibility_rejected', self._space_id, mask))

        runtime = _runtime()
        runtime.bigworld.space_data_factory = _RejectTypedSpaceData
        original_create = runtime.offline_map_creator.create

        def create_then_reset_visibility(map_name):
            original_create(map_name)
            runtime.bigworld.spaces[7]._items_visibility_mask = 0

        runtime.offline_map_creator.create = create_then_reset_visibility
        battle = BattleRuntime(runtime)
        start = {
            'round_id': 1, 'map': '01_karelia', 'bot_authority_id': 1,
            'players': [{
                'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                'vehicle': 'ussr:R11_MS-1', 'health': 500}],
            'bots': []}

        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, start, _Client()))

        self.assertIsNone(battle.error)
        self.assertEqual(0,
                         runtime.bigworld.spaces[7].itemsVisibilityMask)
        self.assertIsNone(battle._standard_space_visibility)
        self.assertTrue(battle._space_visibility_warning_reported)
        self.assertIn(
            ('space_visibility_rejected', 7, 1),
            runtime.bigworld.operations)

    def test_battle_page_patch_does_not_overwrite_a_newer_class_patch(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)

        def newer_show_battle_page(unused_loader):
            return 'newer'

        def replace_during_create(unused_map_name):
            runtime.app_loader.showBattlePage()
            type(runtime.app_loader).showBattlePage = \
                newer_show_battle_page
            runtime.offline_map_creator._OfflineMapCreator__setupCamera()

        runtime.offline_map_creator.create = replace_during_create

        battle._create_native_battle_map('01_karelia')

        self.assertIs(
            newer_show_battle_page,
            type(runtime.app_loader).__dict__['showBattlePage'])
        self.assertEqual('newer', runtime.app_loader.showBattlePage())

    def test_map_to_native_vehicle_to_ready_lifecycle(self):
        runtime = _runtime()
        runtime.bigworld.defer_vehicle_entry = True
        battle = BattleRuntime(runtime)
        client = _Client()
        start = {
            'round_id': 1, 'map': '01_karelia', 'bot_authority_id': 1,
            'players': [{
                'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                'vehicle': 'ussr:R11_MS-1', 'health': 500}],
            'bots': []}

        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, start, client))

        self.assertEqual('loading_entities', battle.state)
        self.assertIsNotNone(battle._server.vehicle_id)
        self.assertEqual(
            battle._server.vehicle_id,
            runtime.bigworld.avatar.playerVehicleID)
        self.assertEqual(
            battle._server.vehicle_id,
            runtime.bigworld.avatar.arena_dp.player_vehicle_id)
        self.assertEqual(1, runtime.bigworld.avatar.arena_dp.refreshes)
        self.assertEqual(
            runtime.constants.ARENA_UPDATE.VEHICLE_ADDED,
            runtime.bigworld.avatar.arena_updates[0][0])
        self.assertIsNone(runtime.bigworld.entity(battle._server.vehicle_id))
        pending = runtime.bigworld.pending_entities[battle._server.vehicle_id]
        self.assertEqual(0.0, pending.rotation[0])
        self.assertEqual(0.0, pending.rotation[1])
        self.assertEqual(battle._local_yaw, pending.rotation[2])
        self.assertEqual([(4, 5)], runtime.app_loader.transitions)

        runtime.bigworld.callbacks.pop(0)()
        runtime.bigworld.enter_pending_vehicle(battle._server.vehicle_id)
        self.assertEqual('loading_entities', battle.state)
        runtime.bigworld.callbacks.pop(0)()

        self.assertEqual('running', battle.state)
        self.assertEqual(1, runtime.bigworld.avatar.vehicle_changed)
        self.assertFalse(battle._server.setClientReady())
        self.assertEqual(500, runtime.bigworld.entity(
            battle._server.vehicle_id).health)

        battle._server.doCmdIntArr(77, 2, [54, 3])
        self.assertEqual(
            [(2, [54, 3])], runtime.compatibility.account_int_commands)
        self.assertEqual((77, 0, ''),
                         runtime.bigworld.avatar.responses[-1])

    def test_client_ready_restores_lakeville_ctf_visibility_after_stock_update(self):
        runtime = _runtime()
        runtime.arena_cache = {7: types.SimpleNamespace(
            geometryName='07_lakeville', gameplayName='ctf', gameplayID=0)}
        runtime.bigworld.defer_vehicle_entry = True
        runtime.bigworld.reset_visibility_before_ready = True
        battle = BattleRuntime(runtime)
        client = _Client()
        start = {
            'round_id': 1, 'map': '07_lakeville', 'bot_authority_id': 1,
            'players': [{
                'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                'vehicle': 'ussr:R11_MS-1', 'health': 500}],
            'bots': []}

        self.assertTrue(battle.start({
            'map': '07_lakeville', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, start, client))
        self.assertEqual(0x00000001,
                         runtime.bigworld.spaces[7].itemsVisibilityMask)

        runtime.bigworld.callbacks.pop(0)()
        runtime.bigworld.enter_pending_vehicle(battle._server.vehicle_id)
        self.assertEqual(0x000fffff,
                         runtime.bigworld.spaces[7].itemsVisibilityMask)
        runtime.bigworld.callbacks.pop(0)()

        self.assertEqual('running', battle.state)
        selected_mask = runtime.bigworld.spaces[7].itemsVisibilityMask
        self.assertEqual(0x00000001, selected_mask)
        visibility_writes = [
            operation[2] for operation in runtime.bigworld.operations
            if operation[0] == 'space_visibility']
        self.assertEqual(
            [0x00000001, 0x000fffff, 0x00000001], visibility_writes)

        # Exact #1513 Lakeville compiled-space base instances use these
        # visibility masks: the CTF bases include bit 0, while the nearby
        # assault2 bases include bit 6 but not bit 0.
        ctf_base_mask = 0xffffff89
        assault2_base_mask = 0xffffffc0
        self.assertTrue(ctf_base_mask & selected_mask)
        self.assertFalse(assault2_base_mask & selected_mask)

        # A still-later stock visibility update must not leak another
        # gameplay's objects after the one-time client-ready repair.  Exact
        # #1513 Malinovka uses bit 1 for its neutral domination control point.
        client_visibility_bit = 0x00100000
        runtime.bigworld.spaces[7].itemsVisibilityMask = \
            client_visibility_bit | 0x00000003
        writes_before_maintenance = len([
            operation for operation in runtime.bigworld.operations
            if operation[0] == 'space_visibility'])
        runtime.bigworld.now += 0.5
        battle._frame()

        self.assertEqual(
            client_visibility_bit | 0x00000001,
            runtime.bigworld.spaces[7].itemsVisibilityMask)
        writes_after_repair = [
            operation for operation in runtime.bigworld.operations
            if operation[0] == 'space_visibility']
        self.assertEqual(
            writes_before_maintenance + 1, len(writes_after_repair))
        self.assertEqual(
            ('space_visibility', 7,
             client_visibility_bit | 0x00000001),
            writes_after_repair[-1])
        malinovka_ctf_base_mask = 0xffffff89
        malinovka_domination_mask = 0xffffff82
        maintained_server_mask = (
            runtime.bigworld.spaces[7].itemsVisibilityMask &
            runtime.client_visibility_flags.SERVER_MASK)
        self.assertEqual(
            client_visibility_bit,
            runtime.bigworld.spaces[7].itemsVisibilityMask &
            runtime.client_visibility_flags.CLIENT_MASK)
        self.assertTrue(
            malinovka_ctf_base_mask &
            maintained_server_mask)
        self.assertFalse(
            malinovka_domination_mask &
            maintained_server_mask)

        # The next periodic read sees a correct mask and performs no native
        # write, keeping this guard observational during normal frames.
        runtime.bigworld.now += 0.5
        battle._frame()
        self.assertEqual(writes_after_repair, [
            operation for operation in runtime.bigworld.operations
            if operation[0] == 'space_visibility'])

    def test_authority_runtime_counts_probes_without_per_probe_clock(self):
        runtime = _runtime()
        runtime.bigworld.defer_vehicle_entry = True
        battle = BattleRuntime(runtime)
        client = _Client()
        start = {
            'round_id': 1, 'map': '01_karelia', 'bot_authority_id': 1,
            'players': [{
                'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                'vehicle': 'ussr:R11_MS-1', 'health': 500}],
            'bots': []}

        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, start, client))
        runtime.bigworld.callbacks.pop(0)()
        runtime.bigworld.enter_pending_vehicle(battle._server.vehicle_id)
        runtime.bigworld.callbacks.pop(0)()

        self.assertIsNone(battle._bots._probe_clock)
        before = battle._bots.probe_totals()
        result = battle._bots._probe_direction(
            (0.0, 0.0, 0.0), 0.0, 10.0)
        after = battle._bots.probe_totals()

        self.assertEqual({'clear': True, 'collision': False,
                          'water': False, 'slope': 0.0}, result)
        self.assertEqual(1, after[4] - before[4])
        self.assertEqual((0.0,) * len(after),
                         battle._bots.probe_duration_totals())

    def test_hidden_worker_constructs_ten_hz_bot_control(self):
        runtime = _runtime()
        runtime.bigworld.defer_vehicle_entry = True
        battle = BattleRuntime(runtime)
        client = _Client()
        client.player_id = -1
        client.name = 'Worker'
        client.bot_authority_id = -1
        client.is_bot_authority = lambda: True
        client.send_bot_manifest = lambda *unused: True
        start = {
            'round_id': 1, 'map': '01_karelia',
            'bot_authority_id': -1, 'players': [], 'bots': []}

        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Worker', 'worker_mode': True}, start, client))
        runtime.bigworld.callbacks.pop(0)()
        runtime.bigworld.enter_pending_vehicle(battle._server.vehicle_id)
        runtime.bigworld.callbacks.pop(0)()

        self.assertEqual('running', battle.state)
        self.assertTrue(battle._bots._fixed_control)
        self.assertEqual(
            bot_runtime.WORKER_CONTROL_SECONDS,
            battle._bots._control_seconds)

    def test_player_identity_sync_rejects_arena_dp_mismatch(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.playerVehicleID = 10
        battle._avatar.guiSessionProvider.getArenaDP = lambda: (
            types.SimpleNamespace(
                isRequiredDataExists=lambda: True,
                getPlayerVehicleID=lambda forceUpdate=True: 11))

        with self.assertRaisesRegex(RuntimeError, 'refresh mismatch'):
            battle._synchronise_player_identity(10)

    def test_player_identity_sync_refreshes_exact_1513_zero_cache(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.playerVehicleID = 10
        arena_dp = battle._avatar.arena_dp

        self.assertEqual(0, arena_dp.getPlayerVehicleID(True))
        self.assertEqual(0, arena_dp.refreshes)
        self.assertTrue(battle._synchronise_player_identity(10))
        self.assertEqual(10, arena_dp.getPlayerVehicleID(False))
        self.assertEqual(1, arena_dp.refreshes)
        self.assertEqual(
            10, runtime.compatibility.marker_player_vehicle_id)

    def test_player_identity_sync_requires_current_bound_avatar(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.playerVehicleID = 10
        arena_dp = battle._avatar.arena_dp
        runtime.bigworld.avatar = _Avatar()
        runtime.bigworld.avatar.playerVehicleID = 10

        with self.assertRaisesRegex(RuntimeError, 'BigWorld player changed'):
            battle._synchronise_player_identity(10)
        self.assertEqual(0, arena_dp.refreshes)

    def test_player_identity_sync_requires_avatar_id_before_arena_refresh(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.playerVehicleID = 9
        arena_dp = battle._avatar.arena_dp

        with self.assertRaisesRegex(
                RuntimeError, 'Avatar player identity mismatch'):
            battle._synchronise_player_identity(10)
        self.assertEqual(0, arena_dp.refreshes)

    def test_player_identity_sync_requires_team_before_arena_refresh(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.playerVehicleID = 10
        battle._avatar.team = 0
        arena_dp = battle._avatar.arena_dp

        with self.assertRaisesRegex(RuntimeError, 'Avatar team is invalid'):
            battle._synchronise_player_identity(10)
        self.assertEqual(0, arena_dp.refreshes)

    def test_local_feedback_rejects_player_identity_drift(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.playerVehicleID = 12
        battle._avatar.arena_dp.player_vehicle_id = 12
        attacker = {
            'engine_id': 10, 'local': True, 'kind': 'player',
            'network_id': 1, 'state': {'team': 1}}
        target = {
            'engine_id': 11, 'local': False, 'kind': 'bot',
            'network_id': 2, 'state': {'team': 2}}

        with self.assertRaisesRegex(RuntimeError, 'identity mismatch'):
            battle._present_combat_feedback({
                'kind': 'bot_hit', 'damage': 50, 'shot_result': 2,
                'dead': False, 'attack_reason': 0, 'death_reason': 0,
                'source': 'shot'}, target, attacker)

    def test_local_feedback_rejects_vehicle_marker_identity_drift(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.playerVehicleID = 10
        battle._synchronise_player_identity(10)
        runtime.compatibility.marker_player_vehicle_id = 9
        attacker = {
            'engine_id': 10, 'local': True, 'kind': 'player',
            'network_id': 1, 'state': {'team': 1}}
        target = {
            'engine_id': 11, 'local': False, 'kind': 'bot',
            'network_id': 2, 'state': {'team': 2}}

        with self.assertRaisesRegex(
                RuntimeError, 'vehicle-marker player identity mismatch'):
            battle._present_combat_feedback({
                'kind': 'bot_hit', 'damage': 50, 'shot_result': 2,
                'dead': False, 'attack_reason': 0, 'death_reason': 0,
                'source': 'shot'}, target, attacker)

    def test_empty_loading_snapshot_cannot_tombstone_authority_bots(self):
        runtime = _runtime()
        runtime.bigworld.defer_vehicle_entry = True
        battle = BattleRuntime(runtime)
        client = _Client()
        player = {
            'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
            'vehicle': 'ussr:R11_MS-1', 'health': 500}
        start = {
            'round_id': 1, 'map': '01_karelia', 'bot_authority_id': 1,
            'players': [player],
            # The server start barrier reserves identities but intentionally
            # has no canonical pose until the authority publishes a manifest.
            'bots': [{
                'id': 11, 'team': 2, 'slot': 0, 'name': 'Enemy 1'}, {
                'id': 12, 'team': 2, 'slot': 1, 'name': 'Enemy 2'}]}

        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player', 'native_remote_vehicles': False},
            start, client))
        battle.on_snapshot({
            'round_id': 1, 'server_tick': 1,
            'players': [player], 'bots': []})
        runtime.bigworld.callbacks.pop(0)()
        runtime.bigworld.enter_pending_vehicle(battle._server.vehicle_id)
        runtime.bigworld.callbacks.pop(0)()

        self.assertIn('bot:11', battle._pending_bot_creates)
        self.assertIn('bot:12', battle._pending_bot_creates)
        manifests = [value[1] for value in client.sent
                     if value[0] == 'manifest']
        self.assertEqual(1, len(manifests))

        # A second empty snapshot can race with the outbound authority
        # manifest. It must not register/tombstone the local lineup either.
        battle.on_snapshot({
            'round_id': 1, 'server_tick': 2,
            'players': [player], 'bots': []})
        self.assertNotIn('bot:11', battle._sync._entities)
        canonical_bots = [dict(
            value, critical={}, combat_revision=0,
            combat_base_revision=0, combat_ack_seq=0,
            combat_fire_elapsed=0.0, combat_fire_timer=0.0)
            for value in manifests[0]]
        battle.on_snapshot({
            'round_id': 1, 'server_tick': 3,
            'players': [player], 'bots': canonical_bots})
        self.assertFalse(battle._sync._entities['bot:11']['dead'])
        self.assertFalse(battle._sync._entities['bot:12']['dead'])

        battle._frame()
        self.assertIn('bot:11', battle._records)
        self.assertNotIn('bot:12', battle._records)
        runtime.bigworld.now += 0.29
        battle._frame()
        self.assertNotIn('bot:12', battle._records)
        runtime.bigworld.now += 0.02
        battle._frame()
        self.assertIn('bot:12', battle._records)

        # VehicleDescr returns unloaded #1513 testers in this full startup
        # fixture. The resolver must admit every bot descriptor before
        # BotRuntime reads its collision dimensions, not merely make an
        # isolated factory unit test pass.
        bot_descriptors = tuple(battle._bots._descriptors.values())
        self.assertEqual(2, len(bot_descriptors))
        for descriptor in bot_descriptors:
            self.assertTrue(all(
                tester.bbox is not None
                for tester in descriptor.getHitTesters()))
            self.assertEqual(
                (1.5, 3.5, -0.8, 2.0),
                tank_collision.chassis_shape(descriptor))

        battle.stop(show_login=False)
        for descriptor in bot_descriptors:
            self.assertNotIn(id(descriptor), tank_collision._SHAPE_CACHE)

    def test_countdown_waits_until_all_bot_presentations_are_ready(self):
        runtime = _runtime()
        runtime.bigworld.defer_vehicle_entry = True
        battle = BattleRuntime(runtime)
        client = _Client()
        client.send_battle_ready = mock.Mock(return_value=True)
        start = {
            'round_id': 1, 'map': '01_karelia', 'bot_authority_id': 1,
            'players': [{
                'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                'vehicle': 'ussr:R11_MS-1', 'health': 500}],
            'bots': [{
                'id': 11, 'team': 2, 'slot': 0, 'name': 'Enemy 1'}, {
                'id': 12, 'team': 2, 'slot': 1, 'name': 'Enemy 2'}]}

        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player', 'native_remote_vehicles': False},
            start, client))
        runtime.bigworld.callbacks.pop(0)()
        runtime.bigworld.enter_pending_vehicle(battle._server.vehicle_id)
        runtime.bigworld.callbacks.pop(0)()

        self.assertEqual(2, len(battle._pending_bot_create_order))
        self.assertFalse(battle._ready_sent)
        periods = [payload for kind, payload
                   in runtime.bigworld.avatar.arena_updates
                   if kind == runtime.constants.ARENA_UPDATE.PERIOD]
        self.assertEqual([], periods)
        battle._frame()

        client.send_battle_ready.assert_not_called()
        self.assertFalse(battle._ready_sent)
        self.assertEqual(1, len(battle._pending_bot_create_order))
        self.assertFalse(battle._battle_live)
        # Enemy models finish behind BattleLoading, but their marker/minimap
        # visual is still not registered before the first real spot.
        self.assertEqual(0, len(runtime.bigworld.avatar.visual_starts))
        enemy = battle._records['bot:11']
        self.assertFalse(enemy['spot_visible'])
        remote = battle._remote_factory.get(enemy['engine_id'])
        self.assertFalse(remote.model.visible)
        self.assertIsNone(runtime.bigworld.entity(enemy['engine_id']))
        self.assertIsNone(runtime.bigworld.entities.get(enemy['engine_id']))
        self.assertNotIn(enemy['engine_id'], runtime.bigworld.entities)

        runtime.bigworld.now += battle_runtime_module.BOT_SPAWN_SECONDS + 0.01
        battle._frame()

        client.send_battle_ready.assert_called_once()
        self.assertTrue(battle._ready_sent)
        self.assertFalse(battle._pending_bot_create_order)

        self.assertTrue(battle._apply_authority_bot_poses([{
            'id': 11, 'alive': True, 'x': 17.0, 'y': 2.0, 'z': 19.0,
            'yaw': 0.75, 'aim_yaw': 0.9, 'gun_pitch': -0.1}]))

        self.assertEqual((17.0, 2.0, 19.0), tuple(remote.position))
        self.assertAlmostEqual(0.75, remote.yaw)
        self.assertAlmostEqual(0.9, remote._aim_yaw)
        self.assertAlmostEqual(-0.1, remote._gun_pitch)
        self.assertIsNone(runtime.bigworld.entity(enemy['engine_id']))
        self.assertIsNone(runtime.bigworld.entities.get(enemy['engine_id']))

        battle._apply_health(enemy, {'health': 125, 'alive': True})

        self.assertEqual(125, remote.health)
        self.assertIsNone(runtime.bigworld.entity(enemy['engine_id']))
        self.assertNotIn(enemy['engine_id'], runtime.bigworld.entities)

        battle._destroy_entity({'entity': 'bot:11'})

        self.assertNotIn('bot:11', battle._records)
        self.assertIsNone(battle._remote_factory.get(enemy['engine_id']))
        self.assertIsNone(runtime.bigworld.entity(enemy['engine_id']))

    def test_wreck_prewarm_is_batched_in_loading_before_runtime_starts(self):
        runtime = _runtime()
        runtime.bigworld.defer_vehicle_entry = True
        original_load = runtime.bigworld.loadResourceListBG
        wreck_requests = []
        runtime.model_assembler.getPartModelsFromDesc = (
            lambda descriptor, state: (
                '%s-%s.model' % (descriptor.name, state),))

        def defer_wrecks(resources, callback):
            if isinstance(resources[0], str):
                wreck_requests.append((resources, callback))
                return
            original_load(resources, callback)

        runtime.bigworld.loadResourceListBG = defer_wrecks
        battle = BattleRuntime(runtime)
        client = _Client()
        start = {
            'round_id': 1, 'map': '01_karelia', 'bot_authority_id': 1,
            'players': [{
                'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                'vehicle': 'ussr:R11_MS-1', 'health': 500}],
            'bots': [{
                'id': 11, 'team': 2, 'slot': 0, 'name': 'Enemy 1'}, {
                'id': 12, 'team': 2, 'slot': 1, 'name': 'Enemy 2'}]}

        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player', 'native_remote_vehicles': False},
            start, client))

        # All selected descriptors submit immediately from _create_entities;
        # no bot-spawn timer has run and engine time has not advanced.
        self.assertTrue(wreck_requests)
        self.assertEqual(10.0, runtime.bigworld.now)
        self.assertEqual(
            sum(len(paths) for paths, unused_callback in wreck_requests),
            battle._remote_factory.wreck_prewarm_pending_count())

        runtime.bigworld.callbacks.pop(0)()
        runtime.bigworld.enter_pending_vehicle(battle._server.vehicle_id)
        runtime.bigworld.callbacks.pop(0)()

        self.assertEqual('loading_entities', battle.state)
        self.assertTrue(runtime.bigworld.callbacks)

        for unused_paths, callback in wreck_requests:
            callback(types.SimpleNamespace(failedIDs=()))
        runtime.bigworld.callbacks.pop(0)()

        self.assertEqual('running', battle.state)
        self.assertEqual(
            0, battle._remote_factory.wreck_prewarm_pending_count())
        battle.stop(show_login=False)

    def test_wreck_prewarm_timeout_degrades_instead_of_blocking_battle(self):
        runtime = _runtime()
        runtime.bigworld.now = 20.0
        battle = BattleRuntime(runtime)
        battle._vehicle_ready_deadline = 19.0
        battle._remote_factory = types.SimpleNamespace(
            wreck_prewarm_pending_count=lambda: 3,
            abandon_pending_wreck_prewarm=mock.Mock(return_value=True))

        self.assertTrue(battle._wreck_prewarm_ready_for_startup())
        battle._remote_factory.abandon_pending_wreck_prewarm.\
            assert_called_once_with()

    def test_direction_probe_copies_dual_distance_three_lane_corridor(self):
        runtime = _runtime()
        rays = []
        original = runtime.bigworld.wg_collideSegment

        def collide(space_id, start, end, mask):
            rays.append((start, end))
            return original(space_id, start, end, mask)

        runtime.bigworld.wg_collideSegment = collide
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar

        result = battle._direction_probe((0.0, 0.0, 0.0), 0.0, 6.0)

        self.assertTrue(result['clear'])
        horizontal = [(start, end) for start, end in rays
                      if abs(start.y - end.y) < 0.001]
        self.assertEqual(6, len(horizontal))
        self.assertEqual(20.0, max(end.z for unused, end in horizontal))
        self.assertEqual({-2.2, 0.0, 2.2},
                         {round(end.x, 1) for unused, end in horizontal})

    def test_direction_probe_stops_at_the_local_navigation_turn(self):
        runtime = _runtime()
        rays = []
        original = runtime.bigworld.wg_collideSegment

        def collide(space_id, start, end, mask):
            rays.append((start, end))
            return original(space_id, start, end, mask)

        runtime.bigworld.wg_collideSegment = collide
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar

        result = battle._direction_probe(
            (0.0, 0.0, 0.0), 0.0, 6.0, None, 4.0)

        self.assertTrue(result['clear'])
        horizontal = [(start, end) for start, end in rays
                      if abs(start.y - end.y) < 0.001]
        self.assertEqual(6, len(horizontal))
        self.assertEqual(4.0, max(end.z for unused, end in horizontal))

    def test_direction_probe_keeps_a_wall_before_the_turn_fail_closed(self):
        runtime = _runtime()
        original = runtime.bigworld.wg_collideSegment

        def collide(space_id, start, end, mask):
            if abs(start.y - end.y) < 0.001:
                return (_Vector(start.x, start.y, start.z + 2.0),)
            return original(space_id, start, end, mask)

        runtime.bigworld.wg_collideSegment = collide
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar

        result = battle._direction_probe(
            (0.0, 0.0, 0.0), 0.0, 0.0, None, 4.0)

        self.assertFalse(result['clear'])
        self.assertTrue(result['collision'])

    def test_direction_probe_preserves_a_clear_downhill_grade_sign(self):
        runtime = _runtime()

        def collide(unused_space, start, end, unused_mask):
            if (abs(start.x - end.x) < 0.001 and
                    abs(start.z - end.z) < 0.001):
                return (_Vector(start.x, -0.1 * start.z, start.z),)
            return None

        runtime.bigworld.wg_collideSegment = collide
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar

        result = battle._direction_probe(
            (0.0, 0.0, 0.0), 0.0, 6.0)

        self.assertTrue(result['clear'])
        self.assertAlmostEqual(-0.1, result['slope'])

    def test_final_motion_world_receipt_copies_exact_flat_ground_geometry(self):
        from gui.mods.offline_lan_0922 import destructibles_sensor

        runtime = _runtime()
        rays = []
        original = runtime.bigworld.wg_collideSegment

        def collide(space_id, start, end, mask):
            rays.append((start, end))
            return original(space_id, start, end, mask)

        runtime.bigworld.wg_collideSegment = collide
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._destructibles = destructibles_sensor

        receipt = battle._direction_world_receipt(
            (0.0, 0.0, 0.0), 0.0, 4.0, _Descriptor())

        self.assertEqual(15.0, receipt['distance'])
        self.assertAlmostEqual(1.6, receipt['half_width'])
        self.assertEqual(3.5, receipt['leading'])
        self.assertEqual((0.0, 0.0, 0.0), receipt['origin'])
        self.assertEqual(0.0, receipt['yaw'])
        self.assertEqual(1, receipt['direction'])
        receipt_rays = [
            (start, end) for start, end in rays
            if (abs(start.y - end.y) < 0.001 and
                abs(start.z + 0.5) < 0.001 and
                abs(end.z - 15.0) < 0.001)
        ]
        self.assertEqual(9, len(receipt_rays))
        self.assertEqual(
            {-1.6, 0.0, 1.6},
            {round(start.x, 1) for start, unused in receipt_rays})
        self.assertEqual(
            {0.6, 1.1, 1.6},
            {round(start.y, 1) for start, unused in receipt_rays})

    def test_final_motion_world_receipt_stops_at_the_local_turn(self):
        from gui.mods.offline_lan_0922 import destructibles_sensor

        runtime = _runtime()
        rays = []
        original = runtime.bigworld.wg_collideSegment

        def collide(space_id, start, end, mask):
            rays.append((start, end))
            return original(space_id, start, end, mask)

        runtime.bigworld.wg_collideSegment = collide
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._destructibles = destructibles_sensor

        receipt = battle._direction_world_receipt(
            (0.0, 0.0, 0.0), 0.0, 4.0, _Descriptor(), 4.0)

        self.assertEqual(4.0, receipt['distance'])
        receipt_rays = [
            (start, end) for start, end in rays
            if (abs(start.y - end.y) < 0.001 and
                abs(start.z + 0.5) < 0.001 and
                abs(end.z - 4.0) < 0.001)
        ]
        self.assertEqual(9, len(receipt_rays))

    def test_reverse_world_receipt_records_exact_travel_heading_and_sign(self):
        from gui.mods.offline_lan_0922 import destructibles_sensor

        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._destructibles = destructibles_sensor

        receipt = battle._direction_world_receipt(
            (4.0, 2.0, 8.0), math.pi, -4.0, _Descriptor())

        self.assertEqual((4.0, 2.0, 8.0), receipt['origin'])
        self.assertAlmostEqual(math.pi, receipt['yaw'])
        self.assertEqual(-1, receipt['direction'])
        self.assertEqual(3.5, receipt['leading'])

    def test_final_world_receipt_catches_narrow_hull_edge_pillar(self):
        from gui.mods.offline_lan_0922 import destructibles_sensor

        runtime = _runtime()
        horizontal_x = []

        def collide(unused_space_id, start, end, unused_mask):
            if abs(start.y - end.y) > 0.1:
                return (_Vector(end.x, 0.0, end.z),)
            horizontal_x.append(round(start.x, 1))
            if abs(start.x - 1.6) < 0.001:
                return (_Vector(start.x, start.y, start.z + 2.0),)
            return None

        runtime.bigworld.wg_collideSegment = collide
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._destructibles = destructibles_sensor

        with mock.patch.object(
                destructibles_sensor, '_catalog_soft_static_path',
                return_value=False) as soft_path:
            result = battle._direction_world_receipt(
                (0.0, 0.0, 0.0), 0.0, 4.0, _Descriptor())

        self.assertFalse(result)
        self.assertIn(1.6, horizontal_x)
        self.assertNotIn(1.6, (-2.2, 0.0, 2.2))
        soft_path.assert_called_once()

    def test_deferred_exact_world_receipt_returns_no_proof(self):
        from gui.mods.offline_lan_0922 import destructibles_sensor

        runtime = _runtime()

        def collide(unused_space_id, start, end, unused_mask):
            if abs(start.y - end.y) > 0.1:
                return (_Vector(end.x, 0.0, end.z),)
            if abs(start.x - 1.6) < 0.001:
                return (_Vector(start.x, start.y, start.z + 2.0),)
            return None

        runtime.bigworld.wg_collideSegment = collide
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._destructibles = destructibles_sensor

        with mock.patch.object(
                destructibles_sensor, '_catalog_soft_static_path',
                return_value='deferred'):
            result = battle._direction_world_receipt(
                (0.0, 0.0, 0.0), 0.0, 4.0, _Descriptor())

        self.assertEqual('deferred', result)

    def test_generic_direction_probe_never_runs_exact_world_receipt(self):
        from gui.mods.offline_lan_0922 import destructibles_sensor

        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._destructibles = destructibles_sensor
        battle._direction_world_receipt = mock.Mock(
            side_effect=AssertionError('planning requested exact receipt'))

        result = battle._direction_probe(
            (0.0, 0.0, 0.0), 0.0, 4.0, _Descriptor())

        self.assertTrue(result['clear'])
        self.assertNotIn('world_receipt', result)
        battle._direction_world_receipt.assert_not_called()

    def test_direction_probe_uses_asymmetric_hull_lead_and_directional_cap(self):
        from gui.mods.offline_lan_0922 import destructibles_sensor

        runtime = _runtime()
        descriptor = _Descriptor()
        descriptor.hull.hitTester.bbox = (
            _Vector(-1.7, -0.2, -2.0),
            _Vector(1.7, 1.4, 5.0), None)
        captures = []

        def collide(unused_space_id, start, end, unused_mask):
            if abs(start.y - end.y) > 0.1:
                return (_Vector(end.x, 0.0, end.z),)
            direction = end - start
            direction.normalise()
            return (start + direction.scale(6.0),)

        def soft_path(unused_space_id, start, unused_end,
                      unused_collision, impact_speed, unused_descriptor,
                      recast_budget=None, allow_kinetic_first=False,
                      kinetic_speed=None):
            # The typed receipt owns a separate exact 3x3 sweep beginning
            # behind the hull. Keep this assertion scoped to the legacy far
            # planning rays whose reachable-impact calculation it verifies.
            if abs(float(start.z)) < 0.001:
                captures.append(float(impact_speed))
                contracts.append((allow_kinetic_first, kinetic_speed))
            return True

        runtime.bigworld.wg_collideSegment = collide
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._destructibles = destructibles_sensor
        params = {
            'mass': 1000.0, 'speedFwd': 20.0, 'speedBwd': 10.0}
        contracts = []

        with mock.patch.object(
                destructibles_sensor, '_catalog_soft_static_path',
                side_effect=soft_path), \
                mock.patch(
                    'gui.mods.offline_lan_0922.battle_runtime.'
                    'vehicle_physics.derive_params', return_value=params), \
                mock.patch(
                    'gui.mods.offline_lan_0922.battle_runtime.'
                    'vehicle_physics.engine_force',
                    side_effect=lambda unused_params, unused_speed, throttle,
                    unused_pitch: 2000.0 * throttle):
            self.assertTrue(battle._direction_probe(
                (0.0, 0.0, 0.0), 0.0, 1.0, descriptor)['clear'])
            forward = tuple(captures)
            forward_contracts = tuple(contracts)
            del captures[:]
            del contracts[:]
            self.assertTrue(battle._direction_probe(
                (0.0, 0.0, 0.0), 0.0, -1.0, descriptor)['clear'])
            reverse = tuple(captures)
            reverse_contracts = tuple(contracts)

        self.assertTrue(forward)
        self.assertTrue(reverse)
        self.assertTrue(all(abs(value - math.sqrt(5.0)) < 0.0001
                            for value in forward))
        self.assertTrue(all(abs(value - math.sqrt(17.0)) < 0.0001
                            for value in reverse))
        self.assertTrue(all(value <= 20.0 for value in forward))
        self.assertTrue(all(value <= 10.0 for value in reverse))
        self.assertTrue(all(enabled and limit == 20.0
                            for enabled, limit in forward_contracts))
        self.assertTrue(all(enabled and limit == 10.0
                            for enabled, limit in reverse_contracts))

    def test_direction_probe_propagates_soft_budget_defer_but_keeps_wall_hard(self):
        from gui.mods.offline_lan_0922 import destructibles_sensor

        runtime = _runtime()
        descriptor = _Descriptor()

        def collide(unused_space_id, start, end, unused_mask):
            if abs(start.y - end.y) > 0.1:
                return (_Vector(end.x, 0.0, end.z),)
            return (_Vector(start.x, start.y, start.z + 4.0),)

        runtime.bigworld.wg_collideSegment = collide
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._destructibles = destructibles_sensor

        with mock.patch.object(
                destructibles_sensor, '_catalog_soft_static_path',
                return_value='deferred'):
            result = battle._direction_probe(
                (0.0, 0.0, 0.0), 0.0, 4.0, descriptor)
        self.assertTrue(result['clear'])
        self.assertFalse(result['collision'])
        self.assertTrue(result['deferred'])

        statuses = iter(('deferred', False))
        with mock.patch.object(
                destructibles_sensor, '_catalog_soft_static_path',
                side_effect=lambda *unused, **unused_kwargs: next(statuses)):
            result = battle._direction_probe(
                (0.0, 0.0, 0.0), 0.0, 4.0, descriptor)
        self.assertFalse(result['clear'])
        self.assertTrue(result['collision'])

    def test_direction_probe_fails_closed_without_hull_bbox_abi(self):
        from gui.mods.offline_lan_0922 import destructibles_sensor

        runtime = _runtime()
        descriptor = _Descriptor()
        descriptor.hull.hitTester.bbox = None

        def collide(unused_space_id, start, end, unused_mask):
            if abs(start.y - end.y) > 0.1:
                return (_Vector(end.x, 0.0, end.z),)
            return (_Vector(start.x, start.y, start.z + 4.0),)

        runtime.bigworld.wg_collideSegment = collide
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._destructibles = destructibles_sensor

        with mock.patch.object(
                destructibles_sensor, '_catalog_soft_static_path') as soft:
            result = battle._direction_probe(
                (0.0, 0.0, 0.0), 0.0, 4.0, descriptor)
        self.assertFalse(result['clear'])
        self.assertTrue(result['collision'])
        soft.assert_not_called()

    def test_bot_firing_lane_trims_hulls_and_tries_two_target_heights(self):
        runtime = _runtime()
        rays = []

        def collide(unused_space_id, start, end, unused_mask):
            rays.append((start, end))
            return (_Vector(start.x + 1.0, start.y, start.z),)

        runtime.bigworld.wg_collideSegment = collide
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        source = {'x': 0.0, 'y': 0.0, 'z': 0.0}
        target = {'position': (0.0, 0.0, 100.0)}

        self.assertFalse(battle._bot_firing_lane(source, target))

        self.assertEqual(2, len(rays))
        self.assertEqual({1.5, 2.2}, {round(end.y, 1)
                                     for unused, end in rays})
        self.assertTrue(all(round(start.z, 1) == 4.0
                            for start, unused in rays))
        self.assertTrue(all(round(end.z, 1) == 96.0
                            for unused, end in rays))

        runtime.bigworld.wg_collideSegment = lambda *unused: None
        self.assertTrue(battle._bot_firing_lane(source, target))

    def test_bot_firing_lane_probes_close_targets_instead_of_assuming_clear(self):
        runtime = _runtime()
        rays = []

        def wall(unused_space_id, start, end, unused_mask):
            rays.append((start, end))
            return (_Vector(start.x, start.y, start.z + 0.25),)

        runtime.bigworld.wg_collideSegment = wall
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        source = {'x': 0.0, 'y': 0.0, 'z': 0.0}
        target = {'position': (0.0, 0.0, 8.0)}

        self.assertFalse(battle._bot_firing_lane(source, target))
        self.assertEqual(2, len(rays))
        self.assertTrue(all(start.z < end.z for start, end in rays))

        runtime.bigworld.wg_collideSegment = lambda *unused: None
        self.assertTrue(battle._bot_firing_lane(source, target))

    def test_bot_friendly_lane_uses_the_frozen_dispersed_parabola(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._worker_mode = True
        battle._avatar = runtime.bigworld.avatar
        descriptor = _Descriptor()
        source_entity = _Vehicle(
            10, descriptor, _Vector(0.0, 0.0, 0.0), (0, 0, 0),
            {'health': 500})
        ally_entity = _Vehicle(
            11, _Descriptor(), _Vector(5.0, 0.0, 100.0), (0, 0, 0),
            {'health': 500})
        chords = []

        def collide_disperse_path(start, end):
            chords.append((start, end))
            return ((types.SimpleNamespace(dist=1.0),)
                    if end.x > 1.0 else ())

        ally_entity.collideSegmentExt = collide_disperse_path
        runtime.bigworld.entities.update({10: source_entity, 11: ally_entity})
        battle._records = {
            'bot:7': {
                'engine_id': 10, 'kind': 'bot', 'network_id': 7,
                'ready': True, 'local': False,
                'state': {'team': 1, 'health': 500, 'alive': True}},
            'bot:8': {
                'engine_id': 11, 'kind': 'bot', 'network_id': 8,
                'ready': True, 'local': False,
                'state': {'team': 1, 'health': 500, 'alive': True}},
        }
        source = {
            'id': 7, 'team': 1, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'fire_seq': 0, 'profile': {'class_tag': 'mediumTank'}}
        target = {'position': (0.0, 0.0, 100.0)}
        launch = {
            'fire_seq': 1, 'shell_index': 0,
            'shot_yaw': 0.05, 'shot_pitch': 0.0,
            'flight_time': 0.125,
            'origin': (0.0, 1.5, 0.0),
        }

        verdict = battle._bot_friendly_firing_lane(
            source, target, descriptor, 0, launch)
        self.assertFalse(verdict['clear'])
        self.assertTrue(chords)
        self.assertGreater(max(end.x for unused, end in chords), 1.0)

        ally_entity.collideSegmentExt = lambda start, end: ()
        self.assertTrue(battle._bot_friendly_firing_lane(
            source, target, descriptor, 0, launch)['clear'])

        ally_entity.collideSegmentExt = lambda *unused: (_ for _ in ()).throw(
            RuntimeError('native hull unavailable'))
        failed = battle._bot_friendly_firing_lane(
            source, target, descriptor, 0, launch)
        self.assertFalse(failed['clear'])
        self.assertNotIn('blocker_id', failed)
        ally_entity.collideSegmentExt = lambda start, end: ()

        # The worker's private player:-1 native-space carrier must not become
        # an artificial friendly-fire obstruction.
        dummy = _Vehicle(
            12, _Descriptor(), _Vector(0.0, 0.0, 20.0), (0, 0, 0),
            {'health': 1})
        dummy.collideSegmentExt = lambda start, end: (
            types.SimpleNamespace(dist=20.0),)
        runtime.bigworld.entities[12] = dummy
        battle._records['player:-1'] = {
            'engine_id': 12, 'kind': 'player', 'network_id': -1,
            'ready': True, 'local': True,
            'state': {'team': 1, 'health': 1, 'alive': True}}
        self.assertTrue(battle._bot_friendly_firing_lane(
            source, target, descriptor, 0, launch)['clear'])

    def test_worker_spg_launch_keeps_exact_remote_muzzle_node(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._worker_mode = True
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(
            10, _Descriptor(), _Vector(4.0, 2.0, 6.0), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._records = {'bot:7': {
            'engine_id': 10, 'kind': 'bot', 'network_id': 7,
            'ready': True, 'local': False,
            'state': {'team': 1, 'health': 500, 'alive': True}}}
        receipt = {'path': ((4.0, 3.5, 6.0), (4.0, 1.0, 100.0))}
        battle._artillery = types.SimpleNamespace(
            request_launch=mock.Mock(return_value=(True, receipt)))

        result = battle._bot_artillery_launch(
            {
                'id': 7, 'x': 4.0, 'y': 2.0, 'z': 6.0,
                'yaw': 0.0, 'pitch': 0.0, 'roll': 0.0,
                'turret_yaw': 0.0, 'gun_pitch': 0.1,
            }, {'position': (4.0, 0.0, 100.0)},
            entity.typeDescriptor, 0, 3, 0.0, 0.1, 2.0, 10.0)

        self.assertIs(receipt, result)
        args = battle._artillery.request_launch.call_args.args
        self.assertEqual((4.0, 3.5, 6.0), args[5])

    def test_stalled_bot_launch_uses_its_logical_barrel_pose(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        descriptor = _Descriptor()
        entity = _Vehicle(
            10, descriptor, _Vector(4.0, 2.0, 6.0), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities[10] = entity
        presented = {
            'x': 4.0, 'y': 2.0, 'z': 6.0, 'yaw': 0.0,
            'pitch': 0.0, 'roll': 0.0, 'turret_yaw': 0.0,
            'gun_pitch': 0.0,
        }
        battle._records = {'bot:7': {
            'engine_id': 10, 'kind': 'bot', 'network_id': 7,
            'ready': True, 'local': False,
            'projectile_collision_pose': dict(presented),
            'state': {'team': 1, 'health': 500, 'alive': True},
        }}

        aligned = battle._bot_direct_launch_origin(
            dict(presented, id=7), descriptor, 0, 1, 0.0, 0.0, 1.0)
        moved = battle._bot_direct_launch_origin(
            dict(presented, id=7, x=9.0),
            descriptor, 0, 2, 0.0, 0.0, 1.0)

        self.assertEqual((4.0, 3.5, 6.0), aligned)
        self.assertEqual((9.0, 3.5, 8.0), moved)

    def test_bot_projectile_sends_frozen_logical_time_and_pose(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        descriptor = _Descriptor()
        entity = _Vehicle(
            10, descriptor, _Vector(4.0, 2.0, 6.0), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._records = {'bot:7': {
            'engine_id': 10, 'kind': 'bot', 'network_id': 7,
            'ready': True, 'local': False,
            'state': {'team': 1, 'health': 500, 'alive': True},
        }}
        battle.client = types.SimpleNamespace(
            authority_epoch=4,
            send_projectile_launch=mock.Mock(return_value=3))
        launch_pose = (4.0, 2.0, 6.0, 0.0, 0.0, 0.0)

        self.assertTrue(battle._launch_bot_projectile({
            'id': 7, 'fire_seq': 3, 'shell_index': 0,
            'shot_yaw': 0.0, 'shot_pitch': 0.0,
            'shot_origin': (4.0, 3.5, 6.0),
            'burst_group_seq': 2, 'burst_index': 1, 'burst_count': 3,
            'launch_time_us': 240000, 'launch_pose': launch_pose,
            'profile': {'class_tag': 'mediumTank'},
        }, 3))

        kwargs = battle.client.send_projectile_launch.call_args.kwargs
        self.assertEqual(240000, kwargs['launch_time_us'])
        self.assertEqual(launch_pose, kwargs['launch_pose'])

    def test_spg_friendly_lane_uses_exact_arc_and_real_he_radius(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        descriptor = _Descriptor()
        descriptor.gun.shots[0].shell.kind = 'HIGH_EXPLOSIVE'
        descriptor.gun.shots[0].shell.explosionRadius = 6.0
        source_entity = _Vehicle(
            10, descriptor, _Vector(0.0, 0.0, 0.0), (0, 0, 0),
            {'health': 500})
        ally_entity = _Vehicle(
            11, _Descriptor(), _Vector(0.0, 20.0, 50.0), (0, 0, 0),
            {'health': 500})
        ally_entity.collideSegmentExt = mock.Mock(return_value=(
            types.SimpleNamespace(dist=1.0),))
        runtime.bigworld.entities.update({10: source_entity, 11: ally_entity})
        battle._records = {
            'bot:7': {
                'engine_id': 10, 'kind': 'bot', 'network_id': 7,
                'ready': True, 'local': False,
                'state': {'team': 1, 'health': 500, 'alive': True}},
            'bot:8': {
                'engine_id': 11, 'kind': 'bot', 'network_id': 8,
                'ready': True, 'local': False,
                'state': {'team': 1, 'health': 500, 'alive': True}},
        }
        source = {'id': 7, 'team': 1}
        receipt = {'path': (
            (0.0, 1.5, 0.0), (0.0, 20.0, 50.0),
            (0.0, 1.0, 100.0))}

        self.assertFalse(battle._bot_artillery_friendly_lane(
            source, {}, descriptor, 0, receipt)['clear'])
        self.assertEqual(1, ally_entity.collideSegmentExt.call_count)

        ally_entity.collideSegmentExt.reset_mock()
        ally_entity.collideSegmentExt.return_value = ()
        ally_entity.position = _Vector(5.0, 0.0, 100.0)
        self.assertFalse(battle._bot_artillery_friendly_lane(
            source, {}, descriptor, 0, receipt)['clear'])
        ally_entity.collideSegmentExt.assert_not_called()

        descriptor.gun.shots[0].shell.explosionRadius = 4.0
        self.assertTrue(battle._bot_artillery_friendly_lane(
            source, {}, descriptor, 0, receipt)['clear'])
        self.assertFalse(battle._bot_artillery_friendly_lane(
            source, {}, descriptor, 0, {'path': ()})['clear'])

    def test_direction_and_graph_probes_reject_drowning_depth_water(self):
        runtime = _runtime()
        runtime.bigworld.wg_collideWater = lambda start, *unused: (
            20.0 if abs(float(start.z)) < 1.0 else 18.5)
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar

        self.assertEqual(1.5, battle._water_depth((0.0, 0.0, 8.0)))
        result = battle._direction_probe((0.0, 0.0, 0.0), 0.0)

        self.assertTrue(result['water'])
        self.assertFalse(result['clear'])
        self.assertIsNone(battle._navigation_ground(0.0, 8.0, 0.0))

    def test_direction_probe_allows_submerged_bot_to_continue_not_deeper(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        cases = (
            ((2.0, 2.0, 2.0), 'equal depth'),
            ((2.0, 1.9, 1.9), 'slightly shallower'),
            ((2.0, 1.5, 0.5), 'shallower escape'),
        )

        for depths, label in cases:
            with self.subTest(label=label):
                battle._water_depth = mock.Mock(side_effect=depths)
                result = battle._direction_probe(
                    (0.0, 0.0, 0.0), 0.0)
                self.assertTrue(result['clear'])
                self.assertFalse(result['water'])
                self.assertEqual(3, battle._water_depth.call_count)

    def test_direction_probe_rejects_new_or_deeper_water(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        cases = (
            ((0.0, 0.91), 'dry entry'),
            ((2.0, 2.11), 'deeper water'),
        )

        for depths, label in cases:
            with self.subTest(label=label):
                battle._water_depth = mock.Mock(side_effect=depths)
                result = battle._direction_probe(
                    (0.0, 0.0, 0.0), 0.0)
                self.assertFalse(result['clear'])
                self.assertTrue(result['water'])

        self.assertEqual(
            0.90, battle_runtime_module.BOT_WATER_AVOID_DEPTH)

    def test_visible_drowning_sensor_only_drives_native_warning_ui(self):
        runtime = _runtime()
        runtime.bigworld.serverTime = lambda: 750.0
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        battle._server = types.SimpleNamespace(vehicle_id=10)
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        entity.appearance.waterSensor = object()
        entity.appearance.isInWater = True
        entity.appearance.isUnderwater = True
        runtime.bigworld.entities[10] = entity
        battle._records = {
            'player:1': {
                'engine_id': 10, 'state': {'health': 500, 'alive': True},
                'kind': 'player', 'network_id': 1, 'local': True}}
        battle._water_depth = mock.Mock(
            side_effect=AssertionError('native water sensor was bypassed'))
        battle._local_last_attacker = ('player', 9)
        self.assertTrue(battle._tick_drowning(0.3, 1.0))
        self.assertEqual((10, 4, 2, (750.0, 10.0)),
                         battle._avatar.misc_status)
        for index in range(34):
            battle._tick_drowning(0.3, 1.3 + index * 0.3)

        self.assertEqual(500, entity.health)
        self.assertTrue(entity.isCrewActive)
        self.assertFalse(getattr(entity, '_drowned', False))
        self.assertEqual(
            {'health': 500, 'alive': True},
            battle._records['player:1']['state'])
        self.assertIsNone(getattr(battle._avatar, 'health_update', None))
        self.assertIsNone(battle._local_damage_report)
        battle._water_depth.assert_not_called()

    def test_native_drowning_sensor_resets_before_a_second_countdown(self):
        runtime = _runtime()
        server_time = [750.0]
        runtime.bigworld.serverTime = lambda: server_time[0]
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._server = types.SimpleNamespace(vehicle_id=10)
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._records = {
            'player:1': {
                'engine_id': 10, 'state': {'health': 500, 'alive': True},
                'kind': 'player', 'network_id': 1, 'local': True}}
        battle._water_depth = mock.Mock(return_value=0.55)

        battle._tick_drowning(0.3, 1.0)
        entity.appearance.waterSensor = object()
        entity.appearance.isInWater = True
        entity.appearance.isUnderwater = True
        battle._water_depth.side_effect = AssertionError(
            'native water sensor was bypassed')
        battle._tick_drowning(0.3, 1.3)
        self.assertEqual(0.3, battle._drown_time)

        entity.appearance.isUnderwater = False
        battle._tick_drowning(0.3, 1.6)
        self.assertEqual(0.0, battle._drown_time)
        self.assertIsNone(battle._drown_started)

        server_time[0] = 760.0
        entity.appearance.isUnderwater = True
        battle._tick_drowning(0.3, 1.9)
        entity.appearance.isUnderwater = False
        entity.appearance.isInWater = False
        battle._tick_drowning(0.3, 2.2)

        self.assertEqual([
            (10, 4, 1, (0.0, 0.0)),
            (10, 4, 2, (750.0, 10.0)),
            (10, 4, 1, (0.0, 0.0)),
            (10, 4, 2, (760.0, 10.0)),
            (10, 4, 0, (0.0, 0.0)),
        ], battle._avatar.misc_statuses)
        self.assertEqual(1, battle._water_depth.call_count)

    def test_hidden_worker_publishes_player_water_observation(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        sender = mock.Mock(return_value=True)
        battle.client = types.SimpleNamespace(
            is_bot_authority=lambda: True,
            send_player_environment=sender)
        battle._worker_mode = True
        entity = _Vehicle(10, _Descriptor(), _Vector(2.0, 0.0, 3.0),
                          (0, 0, 0), {'health': 500})
        entity.appearance.waterSensor = object()
        entity.appearance.isInWater = True
        entity.appearance.isUnderwater = True
        runtime.bigworld.entities[10] = entity
        battle._records = {'player:1': {
            'engine_id': 10,
            'state': {
                'health': 500, 'alive': True, 'input_seq': 12,
                'x': 2.0, 'y': 0.0, 'z': 3.0},
            'kind': 'player', 'network_id': 1, 'local': False}}

        self.assertTrue(battle._publish_player_environment(0.3, 1.0))

        sender.assert_called_once_with([{
            'player_id': 1, 'input_seq': 12, 'level': 2,
        }], 1)
        self.assertEqual(1, battle._player_environment_seq)

    def test_local_overturn_warning_resets_when_the_hull_is_righted(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._server = types.SimpleNamespace(vehicle_id=10)
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._records = {
            'player:1': {
                'engine_id': 10, 'state': {'health': 500, 'alive': True},
                'kind': 'player', 'network_id': 1, 'local': True}}
        battle._local_pitch = math.radians(75.0)

        self.assertFalse(battle._tick_overturn(0.1, 1.0))
        self.assertEqual((10, 3, 1, (0.0, 0.0)),
                         battle._avatar.misc_status)

        battle._local_pitch = 0.0
        self.assertFalse(battle._tick_overturn(0.1, 1.1))
        self.assertEqual((10, 3, 0, (0.0, 0.0)),
                         battle._avatar.misc_status)
        self.assertEqual(0.0, battle._overturn_time)

    def test_local_overturn_never_decides_death_after_thirty_seconds(self):
        runtime = _runtime()
        runtime.bigworld.serverTime = lambda: 900.0
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        battle._server = types.SimpleNamespace(vehicle_id=10)
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._records = {
            'player:1': {
                'engine_id': 10, 'state': {'health': 500, 'alive': True},
                'kind': 'player', 'network_id': 1, 'local': True}}
        battle._local_pitch = math.radians(90.0)
        battle._local_last_attacker = ('player', 9)
        self.assertFalse(battle._tick_overturn(0.1, 1.0))
        self.assertEqual((10, 3, 2, (900.0, 30.0)),
                         battle._avatar.misc_status)
        battle._overturn_time = 29.95
        self.assertFalse(battle._tick_overturn(0.1, 1.1))

        self.assertEqual(500, entity.health)
        self.assertEqual(30.0, battle._overturn_time)
        self.assertIsNone(battle._local_damage_report)

    def test_visible_input_never_carries_a_local_damage_verdict(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._server = types.SimpleNamespace(vehicle_id=10)
        runtime.bigworld.entities[10] = _Vehicle(
            10, _Descriptor(), _Vector(), (0, 0, 0), {'health': 450})
        battle._sender = _LANInputSender(battle)
        gun_state = types.SimpleNamespace(
            shot_index=0, pending_index=1,
            reload_time=0.0, reload_duration=5.0,
            clip=1, clip_size=1, dispersion=0.02)
        battle._gun_state = gun_state
        battle._gun_last_tick = runtime.bigworld.now
        battle._advance_local_gun_to = mock.Mock(return_value=gun_state)
        battle._local_damage_report = {
            'critical': {'events': []}, 'reason': 2,
            'critical_base_revision': 0, 'critical_seq': 1}

        self.assertTrue(battle._sender.send_current())

        self.assertIsNotNone(battle._local_damage_report)
        kwargs = battle.client.sent[-1][2]
        self.assertFalse(any(
            key.startswith('reported_') for key in kwargs))
        self.assertEqual(0, kwargs['shell_index'])
        self.assertEqual(1, kwargs['next_shell_index'])
        self.assertTrue(kwargs['shell_change_pending'])
        battle._advance_local_gun_to.assert_called_once_with(
            runtime.bigworld.entities[10])
        self.assertTrue(battle.acknowledge_local_damage_report(0, 1, 1))
        self.assertIsNone(battle._local_damage_report)

    def test_input_sender_attaches_the_server_mapped_pose_time(self):
        send_input = mock.Mock(return_value=True)
        owner = types.SimpleNamespace(
            local_pose=lambda: ((1.0, 2.0, 3.0), 0.25),
            client=types.SimpleNamespace(send_input=send_input),
            _clock=lambda: 12.5,
            _estimated_motion_time_us=lambda now: int(now * 1000000.0))

        self.assertTrue(_LANInputSender(owner).send_current())

        self.assertEqual(
            12500000, send_input.call_args.kwargs['pose_time_us'])

    def test_snapshot_critical_state_recovers_missed_native_hud_events(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        record = {
            'engine_id': 10, 'state': {'health': 500, 'alive': True},
            'kind': 'player', 'network_id': 1, 'local': True}
        payload = {
            'devices': [{'name': 'engineHealth', 'hp': 0.0,
                         'max_hp': 100.0, 'state': 'destroyed'}],
            'destroyed': ['engineHealth'], 'crew_ko': ['driver'],
            'fire': True, 'ammo_rack_death': False, 'events': []}
        battle._present_critical = mock.Mock(return_value=True)

        bigworld_module = types.ModuleType('BigWorld')
        bigworld_module.player = runtime.bigworld.player
        with mock.patch.dict(sys.modules, {'BigWorld': bigworld_module}):
            self.assertTrue(battle._apply_critical_state(record, payload))

        events = battle._present_critical.call_args.args[1]
        self.assertEqual(
            set([('device', 'destroyed'), ('crew', 'destroyed'),
                 ('fire', True)]),
            set((event['kind'], event['state']) for event in events))
        self.assertTrue(all(event['cause'] == 'shot' for event in events))
        with mock.patch.dict(sys.modules, {'BigWorld': bigworld_module}):
            self.assertFalse(battle._apply_critical_state(record, payload))
        self.assertEqual(1, battle._present_critical.call_count)

    def test_local_critical_echo_does_not_replay_native_hud_event(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        canonical = {
            'devices': [], 'destroyed': [], 'crew_ko': [],
            'fire': False, 'ammo_rack_death': False, 'events': []}
        battle._records = {'player:1': {
            'engine_id': 10, 'state': {
                'health': 500, 'alive': True, 'critical': canonical},
            'critical_state': canonical,
            'kind': 'player', 'network_id': 1, 'local': True}}
        battle._present_critical = mock.Mock(return_value=True)

        self.assertTrue(battle._apply_combat_event({
            'kind': 'health', 'target': 1, 'health': 499,
            'critical': dict(canonical), 'critical_revision': 1,
            'critical_base_revision': 0, 'critical_ack_seq': 1,
            'source': 'client_simulation', 'attack_reason': 0,
            'death_reason': 0}))

        battle._present_critical.assert_not_called()

    def test_local_repair_snapshot_ack_does_not_rewind_live_progress(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        live = {
            'devices': [{'name': 'leftTrackHealth', 'hp': 35.0,
                         'max_hp': 100.0, 'state': 'destroyed'}],
            'destroyed': ['leftTrackHealth'], 'crew_ko': [],
            'fire': False, 'ammo_rack_death': False, 'events': []}
        echoed = dict(live)
        entity.devices_hp = {'leftTrackHealth': 35.0}
        record = {
            'engine_id': 10, 'state': {
                'health': 500, 'alive': True, 'critical': live},
            'critical_state': live, 'critical_revision': 0,
            'kind': 'player', 'network_id': 1, 'local': True}
        battle._local_critical_owned = True
        battle._local_critical_base_revision = 0
        battle._local_critical_next_seq = 1
        battle._local_damage_report = {
            'tracks': [dict(live['devices'][0])],
            'critical_base_revision': 0,
            'critical_seq': 1}

        self.assertFalse(battle._apply_critical_state(record, echoed, {
            'critical_revision': 1, 'critical_base_revision': 0,
            'critical_ack_seq': 1}))

        self.assertEqual(35.0, entity.devices_hp['leftTrackHealth'])
        self.assertEqual(live, record['state']['critical'])
        self.assertIsNone(battle._local_damage_report)
        self.assertTrue(battle._local_critical_owned)

    def test_local_repair_ack_applies_newer_canonical_completion(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        live = {
            'devices': [{'name': 'leftTrackHealth', 'hp': 35.0,
                         'max_hp': 100.0, 'state': 'destroyed'}],
            'destroyed': ['leftTrackHealth'], 'crew_ko': [],
            'fire': False, 'ammo_rack_death': False, 'events': []}
        canonical = {
            'devices': [{'name': 'leftTrackHealth', 'hp': 70.0,
                         'max_hp': 100.0, 'state': 'critical'}],
            'destroyed': [], 'crew_ko': [], 'fire': False,
            'ammo_rack_death': False, 'events': []}
        entity.devices_hp = {'leftTrackHealth': 35.0}
        entity._destroyed_devices = set(['leftTrackHealth'])
        record = {
            'engine_id': 10, 'state': {
                'health': 500, 'alive': True, 'critical': live},
            'critical_state': live, 'critical_revision': 0,
            'kind': 'player', 'network_id': 1, 'local': True}
        battle._present_critical = mock.Mock(return_value=True)
        battle._local_critical_owned = True
        battle._local_critical_base_revision = 0
        battle._local_critical_next_seq = 1
        battle._local_damage_report = {
            'tracks': [dict(live['devices'][0])],
            'critical_base_revision': 0,
            'critical_seq': 1}

        self.assertTrue(battle._apply_critical_state(record, canonical, {
            'critical_revision': 1, 'critical_base_revision': 0,
            'critical_ack_seq': 1}))

        self.assertIsNone(battle._local_damage_report)
        self.assertFalse(battle._local_critical_owned)
        self.assertEqual(70.0, entity.devices_hp['leftTrackHealth'])
        self.assertEqual(canonical, record['state']['critical'])

    def test_duplicate_ordered_event_is_presented_once(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle.state = 'running'
        message = {'events': [{
            'event_id': '1:7:0', 'kind': 'battle_result', 'winner': 2,
            'reason': 'team_eliminated'}]}

        battle.on_events(message)
        battle.on_events(message)

        self.assertEqual(1, len(runtime.bigworld.avatar.round_finished))

    def test_pending_shot_is_accepted_then_applied_once_when_ready(self):
        battle = BattleRuntime(_runtime())
        battle._pending_bot_creates = {
            'bot:11': {'state': {'health': 500, 'alive': True}}}
        battle._pending_bot_create_order = ['bot:11']
        battle._show_shot = mock.Mock(return_value=True)
        message = {'events': [{
            'event_id': '1:7:0', 'kind': 'bot_shot',
            'attacker_bot': 11, 'shell_index': 0}]}

        self.assertTrue(battle.on_events(message))
        self.assertTrue(battle.on_events(message))
        self.assertIn('1:7:0', battle._accepted_event_ids)
        self.assertNotIn('1:7:0', battle._applied_event_ids)
        self.assertEqual(1, len(battle._event_journal))
        battle._show_shot.assert_not_called()

        pending = battle._pending_bot_creates.pop('bot:11')
        battle._pending_bot_create_order = []
        battle._records['bot:11'] = {
            'engine_id': 1000, 'kind': 'bot', 'network_id': 11,
            'state': pending['state'], 'ready': True, 'local': False}

        self.assertTrue(battle._drain_event_journal())
        self.assertIn('1:7:0', battle._applied_event_ids)
        self.assertEqual([], battle._event_journal)
        battle._show_shot.assert_called_once_with(
            message['events'][0], update_state=False)

    def test_pending_combat_merges_state_before_native_presentation(self):
        battle = BattleRuntime(_runtime())
        battle._records = {'player:1': {
            'engine_id': 10, 'state': {'health': 500, 'alive': True},
            'kind': 'player', 'network_id': 1, 'local': True,
            'ready': True}}
        battle._pending_bot_creates = {
            'bot:11': {'state': {'health': 500, 'alive': True}}}
        battle._pending_bot_create_order = ['bot:11']
        battle._apply_combat_event = mock.Mock(return_value=True)
        event = {
            'event_id': '1:8:0', 'kind': 'bot_hit', 'attacker': 1,
            'target_bot': 11, 'health': 0, 'dead': True,
            'source': 'shot', 'attack_reason': 0, 'death_reason': 3}

        self.assertTrue(battle.on_events({'events': [event]}))
        pending = battle._pending_bot_creates['bot:11']
        self.assertEqual(0, pending['state']['health'])
        self.assertFalse(pending['state']['alive'])
        self.assertEqual('player', pending['state']['death_attacker_kind'])
        self.assertEqual(1, pending['state']['death_attacker_id'])
        self.assertIn('1:8:0', battle._accepted_event_ids)
        self.assertNotIn('1:8:0', battle._applied_event_ids)
        battle._apply_combat_event.assert_not_called()

        battle._pending_bot_creates.pop('bot:11')
        battle._pending_bot_create_order = []
        battle._records['bot:11'] = {
            'engine_id': 1000, 'state': pending['state'],
            'kind': 'bot', 'network_id': 11, 'local': False,
            'ready': False}
        self.assertFalse(battle._drain_event_journal())
        battle._records['bot:11']['ready'] = True
        self.assertTrue(battle._drain_event_journal())

        battle._apply_combat_event.assert_called_once_with(
            event, update_state=False)
        self.assertIn('1:8:0', battle._applied_event_ids)

    def test_pending_combat_blocks_snapshot_native_reconciliation(self):
        battle = BattleRuntime(_runtime())
        battle._binding = mock.Mock()
        target = {
            'engine_id': 1000, 'state': {'health': 500, 'alive': True},
            'kind': 'bot', 'network_id': 11, 'local': False,
            'ready': True}
        battle._records = {'bot:11': target}
        battle._pending_bot_creates = {
            'bot:12': {'state': {'health': 500, 'alive': True}}}
        battle._pending_bot_create_order = ['bot:12']
        battle._apply_health = mock.Mock()
        event = {
            'event_id': '1:9:0', 'kind': 'bot_bot_hit',
            'attacker_bot': 12, 'target_bot': 11,
            'health': 250, 'dead': False,
            'source': 'shot', 'attack_reason': 0, 'death_reason': 0}

        self.assertTrue(battle.on_events({'events': [event]}))
        self.assertTrue(battle._materialize_record(target))

        self.assertEqual(250, target['state']['health'])
        battle._apply_health.assert_not_called()
        self.assertNotIn('1:9:0', battle._applied_event_ids)

    def test_keep_corpse_preserves_pending_create_and_live_initial_state(self):
        battle = BattleRuntime(_runtime())
        battle._queue_bot_create({
            'type': 'create', 'entity': 'bot:11', 'kind': 'bot', 'id': 11,
            'state': {'team': 2, 'slot': 0, 'x': 1.0, 'z': 2.0,
                      'health': 500, 'alive': True}})

        battle._destroy_entity({
            'entity': 'bot:11', 'keep_corpse': True,
            'state': {'health': 0, 'alive': False, 'death_reason': 3}})

        pending = battle._pending_bot_creates['bot:11']
        self.assertIn('bot:11', battle._pending_bot_create_order)
        self.assertEqual(500, pending['initial_state']['health'])
        self.assertEqual(0, pending['state']['health'])
        self.assertFalse(pending['state']['alive'])
        self.assertEqual(3, pending['state']['death_reason'])

    def test_unknown_ordered_entity_is_dropped_without_failing_battle(self):
        battle = BattleRuntime(_runtime())
        battle.state = 'running'
        battle._fail = mock.Mock()

        self.assertTrue(battle.on_events({'events': [{
            'event_id': '1:10:0', 'kind': 'bot_shot',
            'attacker_bot': 99}]}))

        self.assertIn('1:10:0', battle._accepted_event_ids)
        self.assertIn('1:10:0', battle._applied_event_ids)
        self.assertEqual([], battle._event_journal)
        battle._fail.assert_not_called()

    def test_unknown_ordered_event_kind_does_not_block_following_event(self):
        battle = BattleRuntime(_runtime())
        battle._avatar = battle._runtime.bigworld.avatar
        battle.state = 'running'
        battle._fail = mock.Mock()

        self.assertTrue(battle.on_events({'events': [
            {'event_id': '1:10:1', 'kind': 'future_magic'},
            {'event_id': '1:10:2', 'kind': 'battle_result', 'winner': 2,
             'reason': 'team_eliminated'},
        ]}))

        self.assertIn('1:10:1', battle._applied_event_ids)
        self.assertIn('1:10:2', battle._applied_event_ids)
        self.assertEqual(1, len(battle._avatar.round_finished))
        battle._fail.assert_not_called()

    def test_ordered_native_exception_is_local_to_that_event(self):
        battle = BattleRuntime(_runtime())
        battle.state = 'running'
        battle._records = {'player:1': {
            'engine_id': 10, 'state': {'health': 500, 'alive': True},
            'kind': 'player', 'network_id': 1, 'local': True,
            'ready': True}}
        battle._show_shot = mock.Mock(
            side_effect=RuntimeError('native shot failed'))
        battle._fail = mock.Mock()

        self.assertTrue(battle.on_events({'events': [{
            'event_id': '1:11:0', 'kind': 'shot', 'attacker': 1}]}))

        self.assertIn('1:11:0', battle._accepted_event_ids)
        self.assertIn('1:11:0', battle._applied_event_ids)
        self.assertEqual([], battle._event_journal)
        battle._fail.assert_not_called()

    def test_snapshot_exception_keeps_last_good_state_and_round_running(self):
        battle = BattleRuntime(_runtime())
        battle.state = 'running'
        battle._last_snapshot = {'server_tick': 7}
        battle._sync = types.SimpleNamespace(
            snapshot=mock.Mock(side_effect=ValueError('stale snapshot')))
        battle._fail = mock.Mock()

        self.assertFalse(battle.on_snapshot({'server_tick': 8}))

        self.assertEqual({'server_tick': 7}, battle._last_snapshot)
        battle._fail.assert_not_called()

    def test_repair_presentation_never_opens_a_client_damage_lineage(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = types.SimpleNamespace(player_id=1)
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._records = {'player:1': {
            'engine_id': 10, 'state': {'health': 500, 'alive': True},
            'kind': 'player', 'network_id': 1, 'local': True}}
        battle._present_critical = mock.Mock(return_value=False)
        battle._present_repair_progress = mock.Mock(return_value=True)
        clock = [100.0]
        battle._clock = lambda: clock[0]
        payload = {
            'devices': [{'name': 'engineHealth', 'hp': 20.0,
                         'max_hp': 100.0, 'state': 'destroyed'}],
            'destroyed': ['engineHealth'], 'crew_ko': [],
            'fire': False, 'ammo_rack_death': False, 'events': []}

        with mock.patch.object(
                critical_damage, 'tick_repair', return_value=payload), \
                mock.patch.object(
                    critical_damage, 'tick_fire', return_value=(0, None)):
            battle._tick_critical_states(0.1)
            self.assertIsNone(battle._local_damage_report)
            clock[0] = 100.2
            battle._tick_critical_states(0.1)
            self.assertIsNone(battle._local_damage_report)
            clock[0] = 101.1
            battle._tick_critical_states(0.1)
            self.assertIsNone(battle._local_damage_report)
            payload['events'] = [{
                'kind': 'device', 'name': 'engineHealth',
                'state': 'critical', 'cause': 'repair'}]
            clock[0] = 101.2
            battle._tick_critical_states(0.1)

        self.assertIsNone(battle._local_damage_report)

    def test_destroyed_track_repair_queues_only_versioned_track_facts(self):
        battle = BattleRuntime(_runtime())
        battle._local_critical_base_revision = 4
        payload = {
            'devices': [
                {'name': 'leftTrackHealth', 'hp': 20.0,
                 'max_hp': 100.0, 'state': 'destroyed'},
                {'name': 'engineHealth', 'hp': 10.0,
                 'max_hp': 100.0, 'state': 'destroyed'},
            ],
            'destroyed': ['leftTrackHealth', 'engineHealth'],
            'crew_ko': ['driver'], 'fire': True,
            'ammo_rack_death': False, 'events': [],
        }

        report = battle._queue_local_track_repair(payload)

        self.assertEqual({
            'tracks': [{
                'name': 'leftTrackHealth', 'hp': 20.0,
                'max_hp': 100.0, 'state': 'destroyed',
            }],
            'critical_base_revision': 4, 'critical_seq': 1,
        }, report)
        self.assertTrue(battle._local_critical_owned)

        payload['devices'][0]['hp'] = 50.0
        payload['devices'][0]['state'] = 'critical'
        payload['destroyed'].remove('leftTrackHealth')
        payload['events'] = [{
            'kind': 'device', 'name': 'leftTrackHealth',
            'old_state': 'destroyed', 'state': 'critical',
            'cause': 'repair',
        }]
        self.assertEqual(2, battle._queue_local_track_repair(
            payload)['critical_seq'])
        self.assertTrue(battle.acknowledge_local_damage_report(4, 2, 2))
        self.assertIsNone(battle._local_damage_report)
        self.assertFalse(battle._local_critical_owned)

    def test_local_track_repair_restores_stock_track_visual_on_completion(self):
        descriptor = _Descriptor()
        descriptor.chassis.maxHealth = 100
        descriptor.chassis.maxRegenHealth = 50
        entity = _Vehicle(10, descriptor, _Vector(), (0, 0, 0),
                          {'health': 500})
        entity.devices_hp = {'leftTrackHealth': 0.0}
        entity._destroyed_devices = set(['leftTrackHealth'])
        entity._critical_devices = set()
        entity.appearance.addCrashedTrack = mock.Mock()
        entity.appearance.delCrashedTrack = mock.Mock()
        loadout = {'has_big_kit': False, 'repair_factor': 0.5}

        partial = BattleRuntime._tick_local_track_repair(
            entity, 5.0, loadout)
        repaired = BattleRuntime._tick_local_track_repair(
            entity, 5.0, loadout)

        self.assertEqual('destroyed', partial['devices'][0]['state'])
        self.assertEqual('critical', repaired['devices'][0]['state'])
        self.assertNotIn('leftTrackHealth', entity._destroyed_devices)
        entity.appearance.delCrashedTrack.assert_called_once_with(True)
        entity.appearance.addCrashedTrack.assert_not_called()

    def test_input_sender_retries_pending_track_repair_separately(self):
        battle = BattleRuntime(_runtime())
        battle.client = _Client()
        battle._sender = _LANInputSender(battle)
        battle._local_damage_report = {
            'tracks': [{
                'name': 'leftTrackHealth', 'hp': 25.0,
                'max_hp': 100.0, 'state': 'destroyed',
            }],
            'critical_base_revision': 4, 'critical_seq': 2,
        }

        self.assertTrue(battle._sender.send_current())

        self.assertEqual('input', battle.client.sent[-2][0])
        self.assertEqual((
            'track_repair', battle._local_damage_report['tracks'], 4, 2),
            battle.client.sent[-1])

        pending = dict(battle._local_damage_report)
        battle.client.send_track_repair = mock.Mock(return_value=False)
        self.assertTrue(battle._sender.send_current())
        self.assertEqual(pending, battle._local_damage_report)
        battle.client.send_track_repair.assert_called_once_with(
            pending['tracks'], 4, 2)

    def test_new_critical_lineage_retires_stale_local_track_repair(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        old = {
            'devices': [{'name': 'leftTrackHealth', 'hp': 25.0,
                         'max_hp': 100.0, 'state': 'destroyed'}],
            'destroyed': ['leftTrackHealth'], 'crew_ko': [],
            'fire': False, 'ammo_rack_death': False, 'events': []}
        newer = dict(old)
        newer['devices'] = [dict(old['devices'][0], hp=0.0)]
        record = {
            'engine_id': 10, 'state': {
                'health': 500, 'alive': True, 'critical': old},
            'critical_state': old, 'critical_revision': 1,
            'kind': 'player', 'network_id': 1, 'local': True}
        battle._present_critical = mock.Mock(return_value=True)
        battle._local_critical_base_revision = 1
        battle._local_critical_server_revision = 1
        battle._local_critical_owned = True
        battle._local_damage_report = {
            'tracks': [dict(old['devices'][0])],
            'critical_base_revision': 1, 'critical_seq': 1}

        self.assertTrue(battle._apply_critical_state(record, newer, {
            'critical_revision': 2, 'critical_base_revision': 2,
            'critical_ack_seq': 0}))

        self.assertIsNone(battle._local_damage_report)
        self.assertFalse(battle._local_critical_owned)
        self.assertEqual(0.0, entity.devices_hp['leftTrackHealth'])

    def test_dead_local_vehicle_stops_repair_and_fire_ticks(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = types.SimpleNamespace(player_id=1)
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._records = {'player:1': {
            'engine_id': 10,
            'state': {'health': 0, 'alive': False,
                      'display_health': 500},
            'kind': 'player', 'network_id': 1, 'local': True}}
        battle._present_repair_progress = mock.Mock()

        with mock.patch.object(critical_damage, 'tick_repair') as repair, \
                mock.patch.object(critical_damage, 'tick_fire') as fire:
            battle._tick_critical_states(0.1)

        repair.assert_not_called()
        fire.assert_not_called()
        battle._present_repair_progress.assert_not_called()
        self.assertIsNone(battle._local_damage_report)

    def test_local_fire_presentation_cannot_override_server_health(self):
        runtime = _runtime()
        send_input = mock.Mock(return_value=True)
        battle = BattleRuntime(runtime)
        battle.client = types.SimpleNamespace(
            player_id=1, send_input=send_input,
            server_capabilities=(
                battle_runtime_module.lan_protocol.
                RAM_CONTACT_LEDGER_CAPABILITY,))
        battle._avatar = runtime.bigworld.avatar
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._binding = mock.Mock()
        battle._sender = _LANInputSender(battle)
        battle._local_position = (0.0, 0.0, 0.0)
        battle._local_yaw = 0.0
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        entity.maxHealth = 500
        entity.devices_hp = {'fuelTankHealth': 0.0}
        entity._destroyed_devices = set(['fuelTankHealth'])
        entity._crew_ko = set()
        entity.is_on_fire = True
        entity._fire_started = 0.0
        entity._fire_timer = 0.0
        runtime.bigworld.entities[10] = entity
        record = {
            'engine_id': 10,
            'state': {'health': 500, 'display_health': 500, 'alive': True},
            'kind': 'player', 'network_id': 1, 'local': True}
        battle._records = {'player:1': record}
        battle._present_critical = mock.Mock(return_value=False)
        battle._present_repair_progress = mock.Mock(return_value=True)
        battle._clock = lambda: 1.0

        battle._tick_critical_states(1.0)

        self.assertEqual(500, entity.health)
        self.assertEqual(500, record['state']['health'])
        self.assertEqual(500, record['state']['display_health'])
        send_input.assert_not_called()

        # Local native callbacks remain presentation-only. Canonical server
        # state wins even when it raises the transient local health value.
        record['ready'] = True
        battle._update_entity({
            'entity': 'player:1',
            'state': {
                'health': 500, 'display_health': 500,
                'alive': True, 'death_reason': 0}})
        self.assertEqual(500, record['state']['health'])
        self.assertEqual(500, record['state']['display_health'])
        self.assertEqual(500, entity.health)
        self.assertEqual(500, battle._avatar.health_update[1])
        self.assertEqual(0, record['state']['death_reason'])

        # A newer canonical reduction is still allowed through.
        with mock.patch.object(battle, '_materialize_record'):
            battle._update_entity({
                'entity': 'player:1',
                'state': {
                    'health': 450, 'display_health': 450,
                    'alive': True, 'death_reason': 0}})
        self.assertEqual(450, record['state']['health'])
        self.assertEqual(450, record['state']['display_health'])

    def test_repair_progress_closes_with_zero_seconds_once(self):
        runtime = _runtime()
        runtime.constants.VEHICLE_MISC_STATUS.\
            DESTROYED_DEVICE_IS_REPAIRING = 17
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        descriptor = _Descriptor()
        extra = types.SimpleNamespace(name='leftTrackHealth')
        descriptor.extrasDict = {'leftTrackHealth': extra}
        descriptor.extras = {3: extra}
        entity = _Vehicle(10, descriptor, _Vector(), (0, 0, 0),
                          {'health': 500})
        entity.devices_hp = {'leftTrackHealth': 25.0}
        entity._destroyed_devices = set(['leftTrackHealth'])

        with mock.patch.object(
                critical_damage._device_damage, 'device_regen_hp',
                return_value=50.0), mock.patch.object(
                    critical_damage._device_damage, 'repair_seconds',
                    return_value=10.0):
            self.assertTrue(battle._present_repair_progress(entity))
            entity._destroyed_devices.clear()
            self.assertTrue(battle._present_repair_progress(entity))
            self.assertTrue(battle._present_repair_progress(entity))

        self.assertEqual([
            (10, 17, 3 | (50 << 8), (5.0,)),
            (10, 17, 3, (0.0,)),
        ], battle._avatar.misc_statuses)

    def test_projectile_damage_sticker_uses_exact_target_sticker_ids(self):
        runtime = _runtime()
        runtime.vehicles.g_cache.shotEffects[3]['targetStickers'] = {
            'armorResisted': 17, 'armorPierced': 29}
        battle = BattleRuntime(runtime)
        descriptor = _Descriptor()
        descriptor.hull.hitTester.localHitTest = mock.Mock(
            return_value=[object()])
        target = _Vehicle(
            10, descriptor, _Vector(), (0.0, 0.0, 0.0),
            {'health': 500})
        shot = descriptor.gun.shots[0]
        collision = types.SimpleNamespace(
            dist=1.0, compName='vehicleHull')
        start = _Vector(0.0, 0.0, -2.0)
        end = _Vector(0.0, 0.0, 2.0)
        decoder = types.SimpleNamespace(decodeSegment=lambda code, unused: (
            'hull', code, _Vector(0.0, 0.0, -1.0),
            _Vector(0.0, 0.0, 1.0)))
        vehicle_effects = types.SimpleNamespace(
            DamageFromShotDecoder=decoder)

        with mock.patch.object(
                battle_runtime_module, 'encode_damage_sticker',
                side_effect=lambda *args, **unused: args[5]) as encode, \
                mock.patch.dict(
                    sys.modules, {'VehicleEffects': vehicle_effects}):
            codes = [battle._projectile_damage_sticker(
                {'local': False, 'native_remote': False}, target, shot,
                start, end, (collision,), result, historic=True)
                     for result in (0, 1, 2)]

        self.assertEqual([17, 17, 29], codes)
        self.assertEqual(
            [17, 17, 29],
            [call.args[5] for call in encode.call_args_list])
        self.assertEqual(3, descriptor.hull.hitTester.localHitTest.call_count)

    def test_present_damage_sticker_uses_stock_appearance_while_hidden(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        target = _Vehicle(
            10, _Descriptor(), _Vector(), (0.0, 0.0, 0.0),
            {'health': 500})
        target.appearance.addDamageSticker = mock.Mock()
        runtime.bigworld.entities[10] = target
        target_record = {
            'engine_id': 10, 'spot_visible': False,
            'kind': 'bot', 'network_id': 2, 'local': False}
        start = _Vector(0.0, 0.0, -1.0)
        end = _Vector(0.0, 0.0, 1.0)
        decoder = types.SimpleNamespace(decodeSegment=mock.Mock(
            return_value=('hull', 29, start, end)))

        with mock.patch.dict(sys.modules, {
                'VehicleEffects': types.SimpleNamespace(
                    DamageFromShotDecoder=decoder)}):
            self.assertTrue(battle._present_damage_sticker(
                {'damage_sticker': 123, 'source': 'shot'}, target_record))
            self.assertFalse(battle._present_damage_sticker(
                {'damage_sticker': 124, 'source': 'shot', 'splash': True},
                target_record))

        target.appearance.addDamageSticker.assert_called_once_with(
            123, 'hull', 29, start, end)

    def test_one_damage_sticker_failure_does_not_disable_later_hits(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        target = _Vehicle(
            10, _Descriptor(), _Vector(), (0.0, 0.0, 0.0),
            {'health': 500})
        target.appearance.addDamageSticker = mock.Mock(side_effect=(
            RuntimeError('one native sticker failed'), None))
        runtime.bigworld.entities[10] = target
        target_record = {'engine_id': 10}
        decoder = types.SimpleNamespace(decodeSegment=mock.Mock(
            return_value=(
                'hull', 29, _Vector(0.0, 0.0, -1.0),
                _Vector(0.0, 0.0, 1.0))))

        with mock.patch.dict(sys.modules, {
                'VehicleEffects': types.SimpleNamespace(
                    DamageFromShotDecoder=decoder)}):
            self.assertFalse(battle._present_damage_sticker(
                {'damage_sticker': 123}, target_record))
            self.assertTrue(battle._present_damage_sticker(
                {'damage_sticker': 124}, target_record))

        self.assertNotIn(
            'projectile damage stickers',
            battle._disabled_optional_features)
        self.assertEqual(2, target.appearance.addDamageSticker.call_count)

    def test_ordered_damage_sticker_contract_is_direct_shot_uint64(self):
        battle = BattleRuntime(_runtime())
        event = {
            'kind': 'hit', 'source': 'shot', 'attacker': 1, 'target': 2,
            'attack_reason': 0, 'death_reason': 0, 'dead': False,
            'damage_sticker': (1 << 64) - 1}

        self.assertEqual(
            ('shot', 0), battle._validate_combat_event_contract(event))

        for invalid in (True, 1.0, -1, 1 << 64):
            with self.subTest(damage_sticker=invalid):
                with self.assertRaisesRegex(
                        RuntimeError, 'invalid damage_sticker'):
                    battle._validate_combat_event_contract(dict(
                        event, damage_sticker=invalid))
        with self.assertRaisesRegex(
                RuntimeError, 'invalid damage_sticker'):
            battle._validate_combat_event_contract(dict(
                event, splash=True))

    def test_local_victim_gets_native_hit_direction_and_world_effect(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        target = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        attacker = _Vehicle(11, _Descriptor(), _Vector(10, 0, 0),
                            (0, 0, 0), {'health': 500})
        runtime.bigworld.entities.update({10: target, 11: attacker})
        battle._local_position = (0.0, 0.0, 0.0)
        target_record = {
            'engine_id': 10, 'state': {'team': 1, 'health': 500},
            'kind': 'player', 'network_id': 1, 'local': True}
        attacker_record = {
            'engine_id': 11,
            'spot_visible': False, 'spot_marker_visible': False,
            'state': {'team': 2, 'health': 500,
                      'x': 10.0, 'y': 0.0, 'z': 0.0},
            'kind': 'bot', 'network_id': 2, 'local': False}
        event = {
            'kind': 'bot_human_hit', 'world_pose': True,
            'x': 0.5, 'y': 1.0, 'z': 0.0, 'shell_index': 0,
            'shot_result': 2, 'damage': 144, 'source': 'shot',
            'dead': False, 'attack_reason': 0, 'death_reason': 0,
            'critical': {'events': [
                {'kind': 'device', 'name': 'engineHealth',
                 'state': 'critical', 'cause': 'shot'},
                {'kind': 'device', 'name': 'leftTrackHealth',
                 'state': 'destroyed', 'cause': 'shot'},
                {'kind': 'crew', 'name': 'gunner1',
                 'state': 'destroyed', 'cause': 'shot'},
                {'kind': 'crew', 'name': 'radioman1',
                 'state': 'destroyed', 'cause': 'shot'},
            ]}}

        self.assertTrue(battle._present_combat_hit(
            event, target_record, attacker_record, 11))
        self.assertTrue(battle._present_combat_feedback(
            event, target_record, attacker_record))

        direction = battle._avatar.hit_directions[-1]
        self.assertEqual((11, 144, 10),
                         (direction[1], direction[2], direction[6]))
        self.assertEqual(
            ((1 << 0) | (1 << (12 + 4)) |
             (1 << (24 + 2)) | (1 << (24 + 3))),
            direction[3])
        self.assertAlmostEqual(-math.pi / 2.0, direction[0])
        effect = battle._avatar.terrainEffects.addNew.call_args
        self.assertEqual(('hitFx', 'hitStages'),
                         (effect.args[1], effect.args[2]))
        self.assertTrue(effect.kwargs['showShockWave'])
        self.assertTrue(effect.kwargs['showFlashBang'])
        self.assertEqual([10, 9], [
            value['eventType']
            for value in battle._avatar.battle_events[0]])

    def test_native_impact_effect_exception_keeps_nonvisual_feedback(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        target = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        attacker = _Vehicle(11, _Descriptor(), _Vector(10, 0, 0),
                            (0, 0, 0), {'health': 500})
        runtime.bigworld.entities.update({10: target, 11: attacker})
        battle._local_position = (0.0, 0.0, 0.0)
        target_record = {
            'engine_id': 10, 'state': {'team': 1, 'health': 500},
            'kind': 'player', 'network_id': 1, 'local': True}
        attacker_record = {
            'engine_id': 11,
            'state': {'team': 2, 'health': 500,
                      'x': 10.0, 'y': 0.0, 'z': 0.0},
            'kind': 'bot', 'network_id': 2, 'local': False}
        event = {
            'kind': 'bot_human_hit', 'world_pose': True,
            'projectile_id': 'bot:2:1',
            'x': 0.5, 'y': 1.0, 'z': 0.0, 'shell_index': 0,
            'shot_result': 2, 'damage': 144, 'source': 'shot'}
        battle._projectile_visual_meta['bot:2:1'] = {'admitted': True}
        add_effect = battle._avatar.terrainEffects.addNew
        add_effect.side_effect = RuntimeError('native impact failed')

        self.assertFalse(battle._present_combat_hit(
            event, target_record, attacker_record, 11))
        add_effect.side_effect = None
        self.assertFalse(battle._present_combat_hit(
            event, target_record, attacker_record, 11))

        self.assertEqual(1, add_effect.call_count)
        self.assertEqual(2, len(battle._avatar.hit_directions))
        self.assertIn(
            'projectile impact presentation',
            battle._disabled_optional_features)

    def test_suppressed_local_muzzle_keeps_stock_shot_handshake(self):
        battle = BattleRuntime(_runtime())
        shooting_extra = types.SimpleNamespace(
            stopFor=mock.Mock(), startFor=mock.Mock())
        descriptor = types.SimpleNamespace(
            extrasDict={'shoot': shooting_extra})
        handshakes = []
        entity = types.SimpleNamespace(typeDescriptor=descriptor)

        def show_shooting(burst_count, is_predicted=False):
            extra = descriptor.extrasDict['shoot']
            extra.stopFor(entity)
            extra.startFor(entity, burst_count)
            handshakes.append((burst_count, is_predicted))
            return True

        entity.showShooting = show_shooting

        self.assertTrue(battle._show_local_shot_without_extra(entity, 3))

        shooting_extra.stopFor.assert_called_once_with(entity)
        shooting_extra.startFor.assert_not_called()
        self.assertEqual([(3, False)], handshakes)
        self.assertIs(
            shooting_extra, descriptor.extrasDict['shoot'])

    def test_he_splash_uses_the_stock_vehicle_explosion_effect(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        target = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        attacker = _Vehicle(11, _Descriptor(), _Vector(10, 0, 0),
                            (0, 0, 0), {'health': 500})
        runtime.bigworld.entities.update({10: target, 11: attacker})
        target_record = {
            'engine_id': 10, 'state': {'health': 500},
            'kind': 'player', 'network_id': 1, 'local': True}
        attacker_record = {
            'engine_id': 11,
            'state': {'health': 500, 'x': 10.0, 'y': 0.0, 'z': 0.0},
            'kind': 'bot', 'network_id': 2, 'local': False}
        event = {
            'kind': 'bot_human_hit', 'world_pose': True,
            'x': 0.5, 'y': 1.0, 'z': 0.0, 'shell_index': 0,
            'shot_result': 2, 'damage': 40, 'source': 'shot',
            'splash': True}

        self.assertTrue(battle._present_combat_hit(
            event, target_record, attacker_record, 11))

        effect = battle._avatar.terrainEffects.addNew.call_args
        self.assertEqual(('splashFx', 'splashStages'),
                         (effect.args[1], effect.args[2]))

    def test_self_splash_with_degenerate_direction_is_presentation_safe(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        vehicle = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                           {'health': 500})
        runtime.bigworld.entities[10] = vehicle
        record = {
            'engine_id': 10, 'state': {'health': 500},
            'kind': 'player', 'network_id': 1, 'local': True}
        event = {
            'kind': 'hit', 'world_pose': True,
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'shell_index': 0,
            'shot_result': 2, 'damage': 40, 'source': 'shot',
            'splash': True}

        self.assertTrue(battle._present_combat_hit(
            event, record, record, 10))

        battle._avatar.terrainEffects.addNew.assert_called_once()
        self.assertEqual([], battle._avatar.hit_directions)

    def test_visible_remote_combat_keeps_the_vehicle_impact_effect(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        target = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        attacker = _Vehicle(11, _Descriptor(), _Vector(10, 0, 0),
                            (0, 0, 0), {'health': 500})
        runtime.bigworld.entities.update({10: target, 11: attacker})
        target_record = {
            'engine_id': 10, 'state': {
                'health': 500, 'x': 0.0, 'y': 0.0, 'z': 0.0},
            'spot_visible': True,
            'kind': 'bot', 'network_id': 1, 'local': False}
        attacker_record = {
            'engine_id': 11,
            'state': {'health': 500, 'x': 10.0, 'y': 0.0, 'z': 0.0},
            'kind': 'bot', 'network_id': 2, 'local': False}
        event = {
            'kind': 'bot_bot_hit', 'world_pose': True,
            'x': 0.5, 'y': 1.0, 'z': 0.0, 'shell_index': 0,
            'shot_result': 2, 'damage': 40, 'source': 'shot'}

        self.assertTrue(battle._present_combat_hit(
            event, target_record, attacker_record, 11))

        battle._avatar.terrainEffects.addNew.assert_called_once()
        self.assertEqual([], battle._avatar.hit_directions)

    def test_direct_ricochet_and_resisted_hits_keep_stock_effects(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        target = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        attacker = _Vehicle(11, _Descriptor(), _Vector(10, 0, 0),
                            (0, 0, 0), {'health': 500})
        runtime.bigworld.entities.update({10: target, 11: attacker})
        target_record = {
            'engine_id': 10, 'state': {'health': 500},
            'kind': 'player', 'network_id': 1, 'local': True}
        attacker_record = {
            'engine_id': 11,
            'state': {'health': 500, 'x': 10.0, 'y': 0.0, 'z': 0.0},
            'kind': 'bot', 'network_id': 2, 'local': False}

        for shot_result, expected in (
                (0, ('ricochetFx', 'ricochetStages')),
                (1, ('resistedFx', 'resistedStages'))):
            battle._avatar.terrainEffects.addNew.reset_mock()
            event = {
                'kind': 'bot_human_hit', 'world_pose': True,
                'x': 0.5, 'y': 1.0, 'z': 0.0, 'shell_index': 0,
                'shot_result': shot_result, 'damage': 0, 'source': 'shot'}

            self.assertTrue(battle._present_combat_hit(
                event, target_record, attacker_record, 11))

            effect = battle._avatar.terrainEffects.addNew.call_args
            self.assertEqual(expected, (effect.args[1], effect.args[2]))

    def test_an_unspotted_target_discloses_no_shot_feedback(self):
        """Impact and damage feedback stay silent until the target is seen."""
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.playerVehicleID = 10
        battle._synchronise_player_identity(10)
        battle._local_position = (0.0, 0.0, 0.0)
        runtime.bigworld.entities.update({
            10: _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                         {'health': 500}),
            11: _Vehicle(11, _Descriptor(), _Vector(10, 0, 0), (0, 0, 0),
                         {'health': 500})})
        target = {
            'engine_id': 11, 'local': False, 'kind': 'bot',
            'network_id': 2, 'spot_visible': False,
            'spot_marker_visible': False,
            'state': {'team': 2, 'health': 500,
                      'x': 10.0, 'y': 0.0, 'z': 0.0}}
        attacker = {
            'engine_id': 10, 'local': True, 'kind': 'player',
            'network_id': 1, 'state': {'team': 1, 'health': 500}}
        base_event = {
            'kind': 'bot_hit', 'world_pose': True,
            'x': 9.5, 'y': 1.0, 'z': 0.0, 'shell_index': 0,
            'attack_reason': 0, 'death_reason': 0, 'source': 'shot'}
        cases = (
            {'shot_result': 2, 'damage': 144, 'dead': False},
            {'shot_result': 1, 'damage': 0, 'dead': False},
            {'shot_result': 1, 'damage': 0, 'dead': False,
             'critical': {'events': [{
                 'kind': 'device', 'name': 'leftTrackHealth',
                 'state': 'destroyed', 'cause': 'shot'}]}},
            {'shot_result': 2, 'damage': 500, 'dead': True},
        )
        for case in cases:
            event = dict(base_event)
            event.update(case)
            self.assertFalse(battle._present_combat_hit(
                event, target, attacker, 10))
            self.assertFalse(battle._present_combat_feedback(
                event, target, attacker))

        battle._avatar.terrainEffects.addNew.assert_not_called()
        self.assertEqual([], battle._avatar.shot_results)
        self.assertEqual([], battle._avatar.battle_events)

        # Radio/team spotting is sufficient for the local damage/shot-result
        # HUD, but not for an armour effect where the hidden model would be.
        target['spot_marker_visible'] = True
        event = dict(base_event)
        event.update(cases[0])
        self.assertFalse(battle._present_combat_hit(
            event, target, attacker, 10))
        self.assertTrue(battle._present_combat_feedback(
            event, target, attacker))
        battle._avatar.terrainEffects.addNew.assert_not_called()
        self.assertEqual(1, len(battle._avatar.shot_results))
        self.assertEqual([7], [
            value['eventType']
            for value in battle._avatar.battle_events[0]])

    def test_a_blind_kill_atomically_publishes_death_and_wreck(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        battle._avatar.playerVehicleID = 10
        battle._synchronise_player_identity(10)
        attacker = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                            {'health': 500})
        target = _Vehicle(11, _Descriptor(), _Vector(0, 0, 10),
                          (0, 0, 0), {'health': 500})
        target.show(False)
        target.appearance.changeVisibility(False)
        target.onHealthChanged = mock.Mock(wraps=target.onHealthChanged)
        runtime.bigworld.entities.update({10: attacker, 11: target})
        target_record = {
            'engine_id': 11, 'local': False, 'kind': 'bot',
            'network_id': 2, 'presentation': True, 'native_remote': True,
            'ready': True, 'visual_started': False,
            'spot_visible': False, 'spot_marker_visible': False,
            'state': {'team': 2, 'health': 500, 'alive': True,
                      'x': 0.0, 'y': 0.0, 'z': 10.0}}
        battle._records = {
            'player:1': {
                'engine_id': 10, 'local': True, 'kind': 'player',
                'network_id': 1, 'presented_frags': 0,
                'state': {'team': 1, 'health': 500, 'alive': True,
                          'frags': 0}},
            'bot:2': target_record,
        }
        request_wreck = mock.Mock(return_value=True)
        battle._remote_factory = types.SimpleNamespace(
            get=lambda engine_id: target if engine_id == 11 else None,
            request_wreck=request_wreck)
        event = {
            'kind': 'bot_hit', 'attacker': 1, 'target_bot': 2,
            'health': 0, 'dead': True, 'attack_reason': 0,
            'death_reason': 0, 'source': 'shot', 'world_pose': True,
            'x': 0.0, 'y': 1.0, 'z': 10.0, 'shell_index': 0,
            'shot_result': 2, 'damage': 500}

        with mock.patch.object(
                critical_damage, 'apply_death',
                return_value=None) as apply_death:
            self.assertTrue(battle._apply_combat_event(event))
            # An ordered replay must not repeat native death/effect callbacks.
            self.assertTrue(battle._apply_combat_event(event))

        self.assertEqual(0, target.health)
        self.assertEqual(0, target_record['state']['health'])
        self.assertNotIn('wreck_known', target_record)
        self.assertNotIn('deferred_health_presentation', target_record)
        self.assertEqual((0, 10, 0), target.health_change)
        self.assertEqual(1, target.onHealthChanged.call_count)
        apply_death.assert_called_once_with(target, 'shot')
        self.assertFalse(battle._set_record_spot_visibility(
            target_record, False, False))
        self.assertTrue(target.model.visible)
        self.assertTrue(target.draw_pass_visible)
        self.assertTrue(target._offlineNativeDrawVisible)
        self.assertEqual([], target.targetCaps)
        request_wreck.assert_called_once_with(11)
        battle._avatar.guiSessionProvider.setVehicleHealth.\
            assert_called_once_with(False, 11, 0, 10, 0)
        battle._avatar.guiSessionProvider.shared.feedback.\
            setVehicleState.assert_not_called()
        battle._binding.arena_vehicle_killed.assert_called_once_with(
            11, 10, 0)
        battle._avatar.terrainEffects.addNew.assert_not_called()
        self.assertEqual([], battle._avatar.shot_results)
        self.assertEqual([], battle._avatar.battle_events)

        statistics = {
            'kind': 'vehicle_statistics', 'actor_kind': 'player',
            'actor_id': 1, 'frags': 1, 'team_killer': False}
        self.assertTrue(battle._apply_vehicle_statistics_event(statistics))
        self.assertFalse(battle._apply_vehicle_statistics_event(statistics))
        battle._binding.arena_vehicle_statistics.assert_called_once_with(
            10, 1)

    def test_blind_critical_state_has_no_remote_vehicle_effect(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(11, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[11] = entity
        hidden = {
            'engine_id': 11, 'local': False,
            'spot_visible': False, 'spot_marker_visible': False}
        ammo_rack = ({
            'kind': 'ammo_rack', 'state': 'destroyed', 'cause': 'shot'},)

        self.assertFalse(battle._present_critical(hidden, ammo_rack, 10))
        self.assertEqual([], entity.ammo_bay_effects)

        hidden['spot_marker_visible'] = True
        self.assertFalse(battle._present_critical(hidden, ammo_rack, 10))
        self.assertEqual([], entity.ammo_bay_effects)

        hidden['spot_visible'] = True
        self.assertTrue(battle._present_critical(hidden, ammo_rack, 10))
        self.assertEqual([(2, 0.0, 0.0)], entity.ammo_bay_effects)

    def test_a_later_spot_reconciles_blind_damage_without_attack_cause(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        entity = _Vehicle(11, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[11] = entity
        record = {
            'engine_id': 11, 'local': False, 'presentation': True,
            'native_remote': True, 'ready': True,
            'spot_visible': False, 'spot_marker_visible': False,
            'visual_started': False,
            'state': {'team': 2, 'health': 400, 'alive': True}}
        battle._remote_factory = types.SimpleNamespace(
            get=lambda engine_id: entity if engine_id == 11 else None)

        battle._apply_health(
            record, record['state'], attacker_id=10, reason_id=0,
            force_cause=True, attack_reason_id=0,
            suppress_combat_presentation=True)

        present_health = battle._avatar.guiSessionProvider.setVehicleHealth
        present_health.assert_not_called()
        self.assertTrue(record['deferred_health_presentation'])

        battle._set_record_spot_visibility(record, False, True)

        present_health.assert_called_once_with(False, 11, 400, 0, 0)
        self.assertNotIn('deferred_health_presentation', record)

    def test_critical_presentation_uses_exact_causes_and_ammo_effect(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        record = {'engine_id': 10, 'local': True}
        events = [
            {'kind': 'device', 'name': 'engineHealth',
             'state': 'destroyed', 'cause': 'fire'},
            {'kind': 'device', 'name': 'leftTrackHealth',
             'state': 'critical', 'cause': 'world_collision'},
            {'kind': 'crew', 'name': 'driver',
             'state': 'destroyed', 'cause': 'drowning'},
            {'kind': 'fire', 'state': False, 'cause': 'repair'},
            {'kind': 'ammo_rack', 'state': 'destroyed', 'cause': 'shot'},
        ]

        with mock.patch.object(
                battle, '_critical_extra_index', return_value=7):
            self.assertTrue(battle._present_critical(record, events, 99))

        self.assertEqual([
            (10, 10, 7, 99, 0), (10, 11, 7, 99, 0),
            (10, 12, 7, 99, 0), (10, 13, 0, 99, 0)],
            battle._avatar.damage_info)
        self.assertEqual([(2, 0.0, 0.0)], entity.ammo_bay_effects)

    def test_expert_is_only_enabled_for_a_finished_active_perk(self):
        finished = types.SimpleNamespace(
            name='commander_expert', level=100.0, isActive=True)
        unfinished = types.SimpleNamespace(
            name='commander_expert', level=99.0, isActive=True)
        inactive = types.SimpleNamespace(
            name='commander_expert', level=100.0, isActive=False)

        self.assertTrue(battle_runtime_module._crew_has_finished_skill(
            [types.SimpleNamespace(skills=(finished,))],
            'commander_expert'))
        self.assertFalse(battle_runtime_module._crew_has_finished_skill(
            [types.SimpleNamespace(skills=(unfinished, inactive))],
            'commander_expert'))

    def test_expert_visibility_uses_exact_misc_status_boundary(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._has_expert = True

        self.assertTrue(battle._enable_expert_visibility())
        self.assertFalse(battle._enable_expert_visibility())

        self.assertEqual([(
            10,
            runtime.constants.VEHICLE_MISC_STATUS.
            OTHER_VEHICLE_DAMAGED_DEVICES_VISIBLE,
            1, (0.0,))], battle._avatar.misc_statuses)

    def test_expert_publishes_extra_indices_after_four_second_lock(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle.state = 'running'
        battle._avatar = runtime.bigworld.avatar
        battle._has_expert = True
        clock = [10.0]
        battle._clock = lambda: clock[0]
        fire = types.SimpleNamespace(name='fire')
        engine = types.SimpleNamespace(name='engineHealth')
        track = types.SimpleNamespace(name='leftTrackHealth')
        descriptor = types.SimpleNamespace(
            extras=(fire, engine, track),
            extrasDict={
                'fire': fire, 'engineHealth': engine,
                'leftTrackHealth': track})
        entity = types.SimpleNamespace(
            id=11, typeDescriptor=descriptor, health=500,
            isCrewActive=True, isAlive=lambda: True)
        runtime.bigworld.entities[11] = entity
        critical = {
            'devices': [
                {'name': 'engineHealth', 'state': 'critical'},
                {'name': 'leftTrackHealth', 'state': 'destroyed'},
            ],
            'destroyed': ['leftTrackHealth'], 'crew_ko': [],
            'fire': True, 'ammo_rack_death': False, 'events': []}
        record = {
            'engine_id': 11, 'local': False, 'ready': True,
            'critical_state': critical,
            'state': {'team': 2, 'health': 500, 'alive': True}}
        battle._records = {'bot:2': record}

        self.assertTrue(battle.monitor_vehicle_damaged_devices(11))
        self.assertFalse(battle._tick_expert_target(13.999))
        self.assertTrue(battle._tick_expert_target(14.0))
        self.assertFalse(battle._tick_expert_target(15.0))
        self.assertEqual([(11, (0, 1), (2,))],
                         battle._avatar.other_vehicle_devices)

        record['critical_state'] = dict(critical)
        record['critical_state']['devices'] = [
            {'name': 'engineHealth', 'state': 'destroyed'},
            {'name': 'leftTrackHealth', 'state': 'destroyed'},
        ]
        record['critical_state']['destroyed'] = [
            'engineHealth', 'leftTrackHealth']
        self.assertTrue(battle._tick_expert_target(15.1))
        self.assertEqual((11, (0,), (1, 2)),
                         battle._avatar.other_vehicle_devices[-1])

        self.assertTrue(battle.monitor_vehicle_damaged_devices(0))
        feedback = battle._avatar.guiSessionProvider.shared.feedback
        feedback.hideVehicleDamagedDevices.assert_called_once_with(11)

    def test_server_hit_uses_stock_shot_result_and_battle_feedback(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.playerVehicleID = 10
        battle._synchronise_player_identity(10)
        target = {
            'engine_id': 11, 'local': False, 'kind': 'bot',
            'network_id': 2, 'state': {'team': 2}}
        attacker = {
            'engine_id': 10, 'local': True, 'kind': 'player',
            'network_id': 1, 'state': {'team': 1}}
        event = {
            'kind': 'bot_hit', 'damage': 144, 'shot_result': 2,
            'dead': False, 'attack_reason': 0, 'death_reason': 0,
            'source': 'shot',
            'critical': {'events': [{
                'kind': 'device', 'name': 'engineHealth',
                'state': 'critical', 'cause': 'shot'}]}}

        self.assertTrue(battle._present_combat_feedback(
            event, target, attacker))

        self.assertEqual(1, len(battle._avatar.shot_results))
        packed = battle._avatar.shot_results[0][0]
        self.assertEqual(11, packed & 0xffffffff)
        flags = runtime.constants.VEHICLE_HIT_FLAGS
        self.assertEqual(
            flags.ATTACK_IS_DIRECT_PROJECTILE |
            flags.MATERIAL_WITH_POSITIVE_DF_PIERCED_BY_PROJECTILE |
            flags.DEVICE_DAMAGED_BY_PROJECTILE,
            packed >> 32)
        self.assertEqual([7, 6], [
            value['eventType']
            for value in battle._avatar.battle_events[0]])

    def test_hidden_worker_never_invokes_stock_combat_feedback(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._worker_mode = True
        battle._avatar = runtime.bigworld.avatar
        runtime.battle_feedback_common = None

        self.assertFalse(battle._present_combat_feedback({}, {}, {}))

        self.assertEqual([], battle._avatar.shot_results)
        self.assertEqual([], battle._avatar.battle_events)

    def test_shot_results_include_confirmed_track_gun_and_fire_flags(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.playerVehicleID = 10
        battle._synchronise_player_identity(10)
        target = {
            'engine_id': 11, 'local': False, 'kind': 'bot',
            'network_id': 2, 'state': {'team': 2}}
        attacker = {
            'engine_id': 10, 'local': True, 'kind': 'player',
            'network_id': 1, 'state': {'team': 1}}
        event = {
            'kind': 'bot_hit', 'damage': 0, 'shot_result': 1,
            'dead': False, 'attack_reason': 0, 'death_reason': 0,
            'source': 'shot', 'critical': {'events': [
                {'kind': 'device', 'name': 'leftTrackHealth',
                 'state': 'destroyed', 'cause': 'shot'},
                {'kind': 'device', 'name': 'gunHealth',
                 'state': 'critical', 'cause': 'shot'},
                {'kind': 'fire', 'state': True, 'cause': 'shot'},
            ]}}

        self.assertTrue(battle._present_combat_feedback(
            event, target, attacker))

        packed = battle._avatar.shot_results[0][0]
        flags = runtime.constants.VEHICLE_HIT_FLAGS
        self.assertEqual(11, packed & 0xffffffff)
        self.assertEqual(
            flags.ATTACK_IS_DIRECT_PROJECTILE |
            flags.MATERIAL_WITH_POSITIVE_DF_NOT_PIERCED_BY_PROJECTILE |
            flags.DEVICE_DAMAGED_BY_PROJECTILE |
            flags.CHASSIS_DAMAGED_BY_PROJECTILE |
            flags.GUN_DAMAGED_BY_PROJECTILE |
            flags.FIRE_STARTED,
            packed >> 32)

    def test_splash_uses_external_explosion_and_explosion_module_flags(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.playerVehicleID = 10
        battle._synchronise_player_identity(10)
        target = {
            'engine_id': 11, 'local': False, 'kind': 'bot',
            'network_id': 2, 'state': {'team': 2}}
        attacker = {
            'engine_id': 10, 'local': True, 'kind': 'player',
            'network_id': 1, 'state': {'team': 1}}
        event = {
            'kind': 'bot_hit', 'damage': 80, 'shot_result': 2,
            'dead': False, 'attack_reason': 0, 'death_reason': 0,
            'source': 'shot', 'splash': True,
            'critical': {'events': [
                {'kind': 'device', 'name': 'leftTrackHealth',
                 'state': 'destroyed', 'cause': 'explosion'},
                {'kind': 'device', 'name': 'gunHealth',
                 'state': 'critical', 'cause': 'explosion'},
                {'kind': 'fire', 'state': True, 'cause': 'explosion'},
            ]}}

        self.assertTrue(battle._present_combat_feedback(
            event, target, attacker))

        packed = battle._avatar.shot_results[0][0]
        flags = runtime.constants.VEHICLE_HIT_FLAGS
        self.assertEqual(11, packed & 0xffffffff)
        self.assertEqual(
            flags.ATTACK_IS_EXTERNAL_EXPLOSION |
            flags.MATERIAL_WITH_POSITIVE_DF_PIERCED_BY_EXPLOSION |
            flags.DEVICE_DAMAGED_BY_EXPLOSION |
            flags.CHASSIS_DAMAGED_BY_EXPLOSION |
            flags.GUN_DAMAGED_BY_EXPLOSION |
            flags.FIRE_STARTED,
            packed >> 32)

    def test_a_moving_bot_is_fed_belt_speeds_and_a_running_engine(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        vehicle = RemoteVehicle(
            1000, _Descriptor(), {
                'publicInfo': {'team': 2, 'name': 'Bot'},
                'health': 500, 'isCrewActive': True, 'gunAnglesPacked': 0},
            _Vector(), (0.0, 0.0, 0.0), runtime.math)
        vehicle.attach_track_animation(_VehicleFilter(), _TrackScroll(), None)
        battle._remote_factory = types.SimpleNamespace(
            get=lambda entity_id: vehicle if entity_id == 1000 else None,
            track_animation_error=None)
        record = {'engine_id': 1000, 'kind': 'bot', 'network_id': 3}

        battle._update_bot_tracks(
            record, {'id': 3, 'speed': 6.0, 'alive': True, 'health': 500},
            10.0)

        self.assertEqual((ENGINE_MODE_RUNNING, 1), vehicle.track_scroll.mode)
        self.assertEqual((6.0, 6.0), vehicle.track_scroll.external)

        battle._update_bot_tracks(
            record, {'id': 3, 'speed': 0.0, 'alive': False, 'health': 0},
            15.0)
        self.assertEqual((ENGINE_MODE_OFF, 0), vehicle.track_scroll.mode)

    def test_a_pivoting_bot_gets_opposed_belt_speeds(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        vehicle = RemoteVehicle(
            1000, _Descriptor(), {
                'publicInfo': {'team': 2, 'name': 'Bot'},
                'health': 500, 'isCrewActive': True, 'gunAnglesPacked': 0},
            _Vector(), (0.0, 0.0, 0.0), runtime.math)
        vehicle.attach_track_animation(_VehicleFilter(), _TrackScroll(), None)
        battle._remote_factory = types.SimpleNamespace(
            get=lambda entity_id: vehicle if entity_id == 1000 else None,
            track_animation_error=None)
        record = {'engine_id': 1000, 'kind': 'bot', 'network_id': 3}

        # Two accepted poses a tenth of a second apart give the yaw rate.
        battle._bot_pose_relax({'id': 3, 'yaw': 0.0}, 'a', 10.0)
        battle._bot_pose_relax({'id': 3, 'yaw': 0.1}, 'b', 10.1)

        battle._update_bot_tracks(
            record, {'id': 3, 'speed': 0.0, 'alive': True, 'health': 500},
            10.1)

        left, right = vehicle.track_scroll.external
        self.assertLess(left, 0.0)
        self.assertGreater(right, 0.0)
        self.assertAlmostEqual(left, -right)
        # A bot with no forward speed still needs a running engine, or the
        # native tick pins both belts to zero.
        self.assertEqual((ENGINE_MODE_RUNNING, _MOVEMENT_ROTATE_RIGHT),
                         vehicle.track_scroll.mode)

        # The mirrored turn reports the other rotation flag.
        battle._bot_pose_relax({'id': 3, 'yaw': 0.1}, 'c', 10.2)
        battle._bot_pose_relax({'id': 3, 'yaw': 0.0}, 'd', 10.3)
        battle._update_bot_tracks(
            record, {'id': 3, 'speed': 0.0, 'alive': True, 'health': 500},
            10.3)
        self.assertEqual((ENGINE_MODE_RUNNING, _MOVEMENT_ROTATE_LEFT),
                         vehicle.track_scroll.mode)

    def test_the_still_devices_and_view_circle_reach_the_battle_hud(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.playerVehicleID = 10
        descriptor = _Descriptor()
        descriptor.miscAttrs = {'circularVisionRadiusFactor': 1.1}
        descriptor.optionalDevices = (
            types.SimpleNamespace(
                name='coatedOptics', id=(0, 5),
                circularVisionRadiusFactor=1.1),
            types.SimpleNamespace(
                name='camouflageNet', id=(0, 6),
                activateWhenStillSec=3.0),
            types.SimpleNamespace(
                name='stereoscope', id=(0, 4),
                circularVisionRadiusFactor=1.25,
                activateWhenStillSec=3.0),
            None)
        battle._local_descriptor = descriptor
        battle._vision_radius = lambda unused_descriptor, entity=None, \
            still_seconds=0.0, local=False: (
                460.0 if still_seconds >= 3.0 else 405.0)

        # A moving player: both stationary devices are out, and the always-on
        # optic never takes one of the two panel slots.
        battle._publish_local_vision_state(None, 0.0)
        self.assertEqual([{'circularVisionRadius': 405.0}],
                         battle._avatar.attr_updates)
        self.assertEqual([(10, 6, False), (10, 4, False)],
                         battle._avatar.optional_devices)

        # Nothing changed, so neither surface is written again.
        battle._publish_local_vision_state(None, 1.0)
        self.assertEqual(1, len(battle._avatar.attr_updates))
        self.assertEqual(2, len(battle._avatar.optional_devices))

        # Past the activation delay both light and the circle grows.
        battle._publish_local_vision_state(None, 3.0)
        self.assertEqual([{'circularVisionRadius': 405.0},
                          {'circularVisionRadius': 460.0}],
                         battle._avatar.attr_updates)
        self.assertEqual([(10, 6, True), (10, 4, True)],
                         battle._avatar.optional_devices[2:])

        # Moving again puts them back out.
        battle._publish_local_vision_state(None, 0.0)
        self.assertEqual([(10, 6, False), (10, 4, False)],
                         battle._avatar.optional_devices[4:])
        self.assertEqual(6, len(battle._avatar.optional_devices))

    def test_countdown_view_circle_uses_skills_and_stationary_devices_only(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle.client.send_spotted_report = mock.Mock(return_value=True)
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.playerVehicleID = 10
        descriptor = _Descriptor()
        descriptor.turret.circularVisionRadius = 300.0
        descriptor.miscAttrs = {'circularVisionRadiusFactor': 1.1}
        descriptor.optionalDevices = (types.SimpleNamespace(
            name='stereoscope', id=(0, 4),
            circularVisionRadiusFactor=1.25,
            activateWhenStillSec=3.0),)
        battle._local_descriptor = descriptor
        battle._local_position = (0.0, 0.0, 0.0)
        battle._garage_loadout = {'crew': (), 'equipments': ()}
        local = _Vehicle(
            10, descriptor, _Vector(), (0.0, 0.0, 0.0),
            {'health': 500})
        enemy = _Vehicle(
            1000, _Descriptor(), _Vector(100.0, 0.0, 0.0),
            (0.0, 0.0, 0.0), {'health': 500})
        runtime.bigworld.entities.update({10: local, 1000: enemy})
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._records = {
            'player:1': {
                'engine_id': 10, 'kind': 'player', 'network_id': 1,
                'ready': True, 'local': True, 'presentation': True,
                'state': {'team': 1, 'health': 500, 'alive': True}},
            'bot:15': {
                'engine_id': 1000, 'kind': 'bot', 'network_id': 15,
                'ready': True, 'local': False, 'presentation': True,
                'spot_visible': False, 'spot_until': 0.0,
                'spot_next': 0.0,
                'state': {'team': 2, 'health': 500, 'alive': True}},
        }
        battle._spot_line_of_sight = mock.Mock(return_value=True)
        battle._set_record_spot_visibility = mock.Mock()
        battle._publish_spotted_targets = mock.Mock()

        # Exact #1513 factors include the still stereoscope.  The loadout law
        # divides it out until the three-second stationary gate has elapsed.
        factors = {
            'circularVisionRadius': 1.06 * (1.25 / 1.1),
        }
        with mock.patch.object(
                battle_runtime_module.loadout_law, 'attribute_factors',
                return_value=factors):
            runtime.bigworld.now = 10.0
            self.assertFalse(battle._update_spotting(10.0, hud_only=True))
            runtime.bigworld.now = 13.0
            self.assertFalse(battle._update_spotting(13.0, hud_only=True))

        radii = [update['circularVisionRadius']
                 for update in battle._avatar.attr_updates]
        self.assertEqual(2, len(radii))
        self.assertAlmostEqual(300.0 * 1.1 * 1.06, radii[0])
        self.assertAlmostEqual(300.0 * 1.25 * 1.06, radii[1])
        self.assertEqual([(10, 4, False), (10, 4, True)],
                         battle._avatar.optional_devices)
        battle._spot_line_of_sight.assert_not_called()
        battle._set_record_spot_visibility.assert_not_called()
        battle._publish_spotted_targets.assert_not_called()
        battle.client.send_spotted_report.assert_not_called()

    def test_countdown_frame_routes_spotting_to_the_local_hud_only(self):
        runtime = _runtime()
        runtime.bigworld.now = 10.0
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._battle_live = False
        battle._prebattle_deadline = 20.0
        battle._last_frame_time = 9.9
        battle._avatar = runtime.bigworld.avatar
        battle._frame_diagnostics = None
        battle._flush_pending_bot_create = mock.Mock()
        battle._flush_pending_entities = mock.Mock()
        battle._drain_event_journal = mock.Mock()
        battle._maybe_send_battle_ready = mock.Mock()
        battle._tick_critical_states = mock.Mock()
        battle._update_spotting = mock.Mock()
        battle._schedule = mock.Mock()

        battle._frame()

        battle._update_spotting.assert_called_once_with(
            10.0, hud_only=True)
        battle._schedule.assert_called_once_with(0.0, battle._frame)

    def test_worker_countdown_waits_for_server_battle_phase(self):
        battle = BattleRuntime(_runtime())
        battle._worker_mode = True
        battle._prebattle_deadline = 10.0
        battle.client = types.SimpleNamespace(combat_phase='prebattle')

        self.assertFalse(battle._prebattle_transition_ready(10.1))
        battle.client.combat_phase = 'battle'
        self.assertTrue(battle._prebattle_transition_ready(10.1))

        battle._worker_mode = False
        battle.client.combat_phase = 'prebattle'
        self.assertTrue(battle._prebattle_transition_ready(10.1))

    def test_zero_countdown_worker_still_waits_for_server_battle_phase(self):
        battle = BattleRuntime(_runtime())
        battle.state = 'running'
        battle._worker_mode = True
        battle._battle_live = False
        battle._config = {}
        battle.client = types.SimpleNamespace(
            combat_phase='prebattle', combat_deadline=None,
            combat_duration=None)
        battle._binding = mock.Mock()
        battle._reset_prebattle_native_visuals = mock.Mock()
        battle._show_prebattle_crosshair = mock.Mock()

        self.assertTrue(battle.on_battle_live({
            'countdown_seconds': 0.0,
            'battle_duration_seconds': 900.0,
        }))

        self.assertFalse(battle._battle_live)
        self.assertIsNotNone(battle._prebattle_deadline)
        battle._binding.arena_period.assert_not_called()
        battle._reset_prebattle_native_visuals.assert_not_called()
        battle._show_prebattle_crosshair.assert_not_called()

    def test_hidden_worker_battle_transition_skips_native_hud_and_gun(self):
        battle = BattleRuntime(_runtime())
        battle._worker_mode = True
        battle._battle_live = False
        battle._config = {'battleDurationSeconds': 900.0}
        battle.client = types.SimpleNamespace(combat_end_deadline=None)
        battle._binding = mock.Mock()
        battle._avatar = mock.Mock()

        self.assertTrue(battle._begin_battle())

        self.assertTrue(battle._battle_live)
        battle._binding.arena_period.assert_not_called()
        battle._avatar.gunRotator.lock.assert_not_called()

    def test_a_battle_hud_panel_failure_does_not_end_the_round(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.playerVehicleID = 10
        descriptor = _Descriptor()
        descriptor.miscAttrs = {'circularVisionRadiusFactor': 1.1}
        descriptor.optionalDevices = (
            types.SimpleNamespace(
                name='stereoscope', id=(0, 4), activateWhenStillSec=3.0),)
        battle._local_descriptor = descriptor
        battle._vision_radius = lambda unused_descriptor, entity=None, \
            still_seconds=0.0, local=False: 405.0

        def _explode(vehicle_id, device_id, is_on):
            raise AssertionError

        battle._avatar.updateVehicleOptionalDeviceStatus = _explode

        self.assertFalse(battle._publish_local_vision_state(None, 0.0))
        self.assertTrue(battle._vision_feed_failed)
        # The feed stays off, and the round keeps running.
        battle._avatar.updateVehicleOptionalDeviceStatus = (
            lambda *args: self.fail('the disabled feed published again'))
        self.assertFalse(battle._publish_local_vision_state(None, 5.0))

    def test_a_bot_that_neither_moves_nor_turns_idles(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)

        self.assertEqual((ENGINE_MODE_IDLE, 0),
                         battle._bot_engine_mode(True, 0.01, 0.005))
        self.assertEqual((ENGINE_MODE_OFF, 0),
                         battle._bot_engine_mode(False, 6.0, 1.0))
        self.assertEqual(
            (ENGINE_MODE_RUNNING, _MOVEMENT_BACKWARD | _MOVEMENT_ROTATE_LEFT),
            battle._bot_engine_mode(True, -6.0, -1.0))

    def test_a_dying_bot_pushes_the_destroyed_marker_state(self):
        """Vehicle.__onVehicleDeath restyles the marker; without it a dead
        vehicle keeps the live plate and its health bar."""
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        battle._avatar.playerVehicleID = 10
        bot = _Vehicle(11, _Descriptor(), _Vector(0, 0, 1), (0, 0, 0),
                       {'health': 500})
        runtime.bigworld.entities.update({11: bot})
        record = {
            'engine_id': 11, 'state': {'team': 2, 'health': 500},
            'kind': 'bot', 'network_id': 2, 'local': False,
            'presentation': True}
        battle._records = {'bot:2': record}
        feedback = battle._avatar.guiSessionProvider.shared.feedback
        present_health = battle._avatar.guiSessionProvider.setVehicleHealth

        battle._apply_health(record, {'health': 200, 'alive': True})
        feedback.setVehicleState.assert_not_called()

        battle._apply_health(record, {'health': 0, 'alive': False})

        feedback.setVehicleState.assert_called_once_with(
            11, runtime.feedback_event_id.VEHICLE_DEAD, False)
        # Retail presents the health first and the dead state second.
        self.assertLess(
            present_health.call_args_list.index(present_health.call_args),
            len(present_health.call_args_list))
        self.assertEqual(
            (False, 11, 0), present_health.call_args_list[-1][0][:3])

    def test_a_drowned_bot_is_dead_even_with_hull_health_left(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        battle._avatar.playerVehicleID = 10
        bot = _Vehicle(11, _Descriptor(), _Vector(0, 0, 1), (0, 0, 0),
                       {'health': 500})
        runtime.bigworld.entities.update({11: bot})
        record = {
            'engine_id': 11, 'state': {'team': 2, 'health': 500},
            'kind': 'bot', 'network_id': 2, 'local': False,
            'presentation': True}
        battle._records = {'bot:2': record}
        feedback = battle._avatar.guiSessionProvider.shared.feedback

        battle._apply_health(record, {
            'health': 0, 'alive': False, 'crew_active': False,
            'display_health': 300})

        self.assertGreater(bot.health, 0)
        feedback.setVehicleState.assert_called_once_with(
            11, runtime.feedback_event_id.VEHICLE_DEAD, False)

    def test_the_local_player_never_takes_the_remote_dead_state(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        battle._avatar.playerVehicleID = 10
        record = {
            'engine_id': 10, 'state': {'team': 1, 'health': 0},
            'kind': 'player', 'network_id': 1, 'local': True}

        self.assertFalse(battle._present_vehicle_dead(record, False))

        (battle._avatar.guiSessionProvider.shared.feedback
         .setVehicleState.assert_not_called())

    def test_local_ram_of_ally_updates_health_without_projectile_feedback(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        battle._avatar.playerVehicleID = 10
        battle._synchronise_player_identity(10)
        attacker = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                            {'health': 500})
        ally = _Vehicle(11, _Descriptor(), _Vector(0, 0, 1), (0, 0, 0),
                        {'health': 500})
        runtime.bigworld.entities.update({10: attacker, 11: ally})
        battle._records = {
            'player:1': {
                'engine_id': 10, 'state': {'team': 1, 'health': 500},
                'kind': 'player', 'network_id': 1, 'local': True},
            'bot:2': {
                'engine_id': 11, 'state': {'team': 1, 'health': 500},
                'kind': 'bot', 'network_id': 2, 'local': False,
                'presentation': True},
        }

        self.assertTrue(battle._apply_combat_event({
            'kind': 'bot_hit', 'attacker': 1, 'target_bot': 2,
            'damage': 50, 'health': 450, 'dead': False,
            'attack_reason': 2, 'death_reason': 0, 'source': 'ram'}))

        self.assertEqual((450, 10, 2), ally.health_change)
        present_health = battle._avatar.guiSessionProvider.setVehicleHealth
        present_health.assert_called_once_with(False, 11, 450, 10, 2)
        self.assertEqual([], battle._avatar.shot_results)
        self.assertEqual([], battle._avatar.battle_events)
        battle._avatar.terrainEffects.addNew.assert_not_called()

    def test_nonpenetration_does_not_replay_the_previous_damage_number(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        battle._avatar.playerVehicleID = 10
        battle._synchronise_player_identity(10)
        attacker = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                            {'health': 500})
        target = _Vehicle(11, _Descriptor(), _Vector(0, 0, 1),
                          (0, 0, 0), {'health': 500})
        runtime.bigworld.entities.update({10: attacker, 11: target})
        battle._records = {
            'player:1': {
                'engine_id': 10,
                'state': {'team': 1, 'health': 500, 'alive': True},
                'kind': 'player', 'network_id': 1, 'local': True},
            'bot:2': {
                'engine_id': 11,
                'state': {
                    'team': 2, 'health': 500, 'display_health': 500,
                    'alive': True},
                'kind': 'bot', 'network_id': 2, 'local': False,
                'presentation': True},
        }
        battle._last_health[11] = (500, 500, True, 0)

        self.assertTrue(battle._apply_combat_event({
            'kind': 'bot_hit', 'attacker': 1, 'target_bot': 2,
            'damage': 0, 'health': 500, 'dead': False,
            'attack_reason': 0, 'death_reason': 0, 'source': 'shot',
            'world_pose': True, 'x': 0.0, 'y': 0.0, 'z': 1.0,
            'shell_index': 0, 'shot_result': 1}))

        self.assertFalse(hasattr(target, 'health_change'))
        battle._avatar.guiSessionProvider.setVehicleHealth.assert_not_called()
        self.assertEqual(1, len(battle._avatar.shot_results))
        packed = battle._avatar.shot_results[0][0]
        flags = runtime.constants.VEHICLE_HIT_FLAGS
        self.assertTrue(
            (packed >> 32) &
            flags.MATERIAL_WITH_POSITIVE_DF_NOT_PIERCED_BY_PROJECTILE)
        self.assertEqual([], battle._avatar.battle_events)

    def test_snapshots_do_not_overwrite_local_damage_colour(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        battle._avatar.playerVehicleID = 10
        battle._synchronise_player_identity(10)
        attacker = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                            {'health': 500})
        target = _Vehicle(11, _Descriptor(), _Vector(0, 0, 1),
                          (0, 0, 0), {'health': 500})
        runtime.bigworld.entities.update({10: attacker, 11: target})
        target_record = {
            'engine_id': 11,
            'state': {
                'team': 2, 'health': 500, 'display_health': 500,
                'alive': True, 'death_attacker_kind': '',
                'death_attacker_id': 0},
            'kind': 'bot', 'network_id': 2, 'local': False,
            'presentation': True}
        battle._records = {
            'player:1': {
                'engine_id': 10,
                'state': {'team': 1, 'health': 500, 'alive': True},
                'kind': 'player', 'network_id': 1, 'local': True},
            'bot:2': target_record,
        }

        def apply_snapshot(health):
            state = dict(target_record['state'])
            state.update({
                'health': health, 'display_health': health, 'alive': True,
                'death_reason': 0, 'death_attacker_kind': '',
                'death_attacker_id': 0})
            target_record['state'] = state
            battle._apply_health(
                target_record, state,
                battle._death_attacker_engine_id(state), 0)

        self.assertTrue(battle._apply_combat_event({
            'kind': 'bot_hit', 'attacker': 1, 'target_bot': 2,
            'damage': 10, 'health': 490, 'dead': False,
            'attack_reason': 0, 'death_reason': 0, 'source': 'shot',
            'world_pose': True, 'x': 0.0, 'y': 0.0, 'z': 1.0,
            'shell_index': 0, 'shot_result': 2}))
        self.assertEqual(490, target_record['state']['display_health'])
        apply_snapshot(490)

        present_health = battle._avatar.guiSessionProvider.setVehicleHealth
        present_health.assert_called_once_with(False, 11, 490, 10, 0)

        self.assertTrue(battle._apply_combat_event({
            'kind': 'bot_hit', 'attacker': 1, 'target_bot': 2,
            'damage': 10, 'health': 480, 'dead': False,
            'attack_reason': 0, 'death_reason': 0, 'source': 'shot',
            'world_pose': True, 'x': 0.0, 'y': 0.0, 'z': 1.0,
            'shell_index': 0, 'shot_result': 2}))
        apply_snapshot(480)

        self.assertTrue(battle._apply_combat_event({
            'kind': 'bot_hit', 'attacker': 1, 'target_bot': 2,
            'damage': 10, 'health': 470, 'dead': False,
            'attack_reason': 2, 'death_reason': 0, 'source': 'ram'}))
        apply_snapshot(470)

        self.assertTrue(battle._apply_combat_event({
            'kind': 'bot_hit', 'attacker': 1, 'target_bot': 2,
            'damage': 10, 'health': 460, 'dead': False,
            'attack_reason': 0, 'death_reason': 0, 'source': 'shot',
            'world_pose': True, 'x': 0.0, 'y': 0.0, 'z': 1.0,
            'shell_index': 0, 'shot_result': 2}))
        apply_snapshot(460)

        self.assertEqual([
            mock.call(False, 11, 490, 10, 0),
            mock.call(False, 11, 480, 10, 0),
            mock.call(False, 11, 470, 10, 2),
            mock.call(False, 11, 460, 10, 0),
        ], present_health.call_args_list)

    def test_local_ram_of_enemy_uses_ram_efficiency_without_shot_results(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.playerVehicleID = 10
        battle._synchronise_player_identity(10)
        attacker = {
            'engine_id': 10, 'local': True, 'kind': 'player',
            'network_id': 1, 'state': {'team': 1}}
        target = {
            'engine_id': 11, 'local': False, 'kind': 'bot',
            'network_id': 2, 'state': {'team': 2}}

        self.assertTrue(battle._present_combat_feedback({
            'kind': 'bot_hit', 'damage': 50, 'dead': False,
            'attack_reason': 2, 'death_reason': 0, 'source': 'ram'},
            target, attacker))

        self.assertEqual([], battle._avatar.shot_results)
        self.assertEqual([7], [
            value['eventType']
            for value in battle._avatar.battle_events[0]])
        self.assertEqual((50 << 16) | (2 << 9),
                         battle._avatar.battle_events[0][0]['details'])

    def test_local_projectile_at_ally_keeps_stock_ally_hit_only(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.playerVehicleID = 10
        battle._synchronise_player_identity(10)
        attacker = {
            'engine_id': 10, 'local': True, 'kind': 'player',
            'network_id': 1, 'state': {'team': 1}}
        target = {
            'engine_id': 11, 'local': False, 'kind': 'bot',
            'network_id': 2, 'state': {'team': 1}}

        self.assertFalse(battle._present_combat_feedback({
            'kind': 'bot_hit', 'damage': 50, 'shot_result': 2,
            'dead': False, 'attack_reason': 0, 'death_reason': 0,
            'source': 'shot'}, target, attacker))

        self.assertEqual(1, len(battle._avatar.shot_results))
        self.assertEqual(11,
                         battle._avatar.shot_results[0][0] & 0xffffffff)
        self.assertEqual([], battle._avatar.battle_events)

    def test_received_friendly_projectile_has_no_enemy_efficiency_event(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        attacker = {
            'engine_id': 11, 'local': False, 'kind': 'player',
            'network_id': 2, 'state': {'team': 1}}
        target = {
            'engine_id': 10, 'local': True, 'kind': 'player',
            'network_id': 1, 'state': {'team': 1}}

        self.assertFalse(battle._present_combat_feedback({
            'kind': 'hit', 'damage': 50, 'shot_result': 2,
            'dead': False, 'attack_reason': 0, 'death_reason': 0,
            'source': 'shot'}, target, attacker))

        self.assertEqual([], battle._avatar.shot_results)
        self.assertEqual([], battle._avatar.battle_events)

    def test_enemy_ricochet_publishes_exact_tanking_efficiency(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        attacker = {
            'engine_id': 11, 'local': False, 'kind': 'bot',
            'network_id': 2, 'state': {'team': 2}}
        target = {
            'engine_id': 10, 'local': True, 'kind': 'player',
            'network_id': 1, 'state': {'team': 1}}

        self.assertTrue(battle._present_combat_feedback({
            'kind': 'bot_human_hit', 'damage': 0,
            'blocked_damage': 320, 'shot_result': 0,
            'dead': False, 'attack_reason': 0, 'death_reason': 0,
            'source': 'shot'}, target, attacker))

        self.assertEqual([5], [
            value['eventType']
            for value in battle._avatar.battle_events[0]])
        self.assertEqual(11, battle._avatar.battle_events[0][0]['targetID'])
        self.assertEqual(320 << 16,
                         battle._avatar.battle_events[0][0]['details'])

    def test_blocked_efficiency_event_is_not_replayed(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle.state = 'running'
        battle._records = {
            'player:1': {
                'engine_id': 10, 'kind': 'player', 'network_id': 1,
                'local': True, 'ready': True,
                'state': {'team': 1, 'health': 500, 'alive': True}},
            'bot:2': {
                'engine_id': 11, 'kind': 'bot', 'network_id': 2,
                'local': False, 'ready': True,
                'state': {'team': 2, 'health': 500, 'alive': True}},
        }
        battle._server_entity = mock.Mock(return_value=object())
        battle._present_combat_hit = mock.Mock(return_value=False)
        battle._apply_health = mock.Mock(return_value=True)
        message = {'events': [{
            'event_id': '1:12:0', 'kind': 'bot_human_hit',
            'attacker_bot': 2, 'target': 1,
            'damage': 0, 'blocked_damage': 320, 'shot_result': 0,
            'health': 500, 'dead': False, 'attack_reason': 0,
            'death_reason': 0, 'source': 'shot'}]}

        self.assertTrue(battle.on_events(message))
        self.assertTrue(battle.on_events(message))

        self.assertEqual(1, len(battle._avatar.battle_events))
        self.assertEqual([5], [
            value['eventType']
            for value in battle._avatar.battle_events[0]])
        self.assertEqual(320 << 16,
                         battle._avatar.battle_events[0][0]['details'])

    def test_fire_feedback_never_uses_projectile_result_or_impact_effect(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.playerVehicleID = 10
        battle._synchronise_player_identity(10)
        attacker = {
            'engine_id': 10, 'local': True, 'kind': 'player',
            'network_id': 1, 'state': {'team': 1}}
        target = {
            'engine_id': 11, 'local': False, 'kind': 'bot',
            'network_id': 2, 'state': {'team': 2}}
        event = {
            'kind': 'bot_hit', 'damage': 10, 'dead': False,
            'attack_reason': 1, 'death_reason': 0, 'source': 'fire'}

        self.assertFalse(battle._present_combat_hit(
            event, target, attacker, 10))
        self.assertTrue(battle._present_combat_feedback(
            event, target, attacker))

        self.assertEqual([], battle._avatar.shot_results)
        battle._avatar.terrainEffects.addNew.assert_not_called()
        self.assertEqual((10 << 16) | (1 << 9),
                         battle._avatar.battle_events[0][0]['details'])

    def test_combat_attack_reason_is_mandatory_and_matches_source(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)

        with self.assertRaisesRegex(RuntimeError, 'no attack_reason'):
            battle._combat_attack_reason({'source': 'ram'})
        with self.assertRaisesRegex(RuntimeError, 'no source'):
            battle._combat_attack_reason({'attack_reason': 0})
        with self.assertRaisesRegex(RuntimeError, 'does not match source'):
            battle._combat_attack_reason({
                'source': 'ram', 'attack_reason': 0})
        self.assertEqual(2, battle._combat_attack_reason({
            'source': 'ram', 'attack_reason': 2, 'death_reason': 0}))
        self.assertIsNone(battle._combat_attack_reason({
            'source': 'player_left', 'attack_reason': None,
            'death_reason': 0}))
        with self.assertRaisesRegex(RuntimeError, 'null attack_reason'):
            battle._combat_attack_reason({
                'source': 'player_left', 'attack_reason': 0,
                'death_reason': 0})

    def test_combat_source_contract_rejects_implicit_or_mixed_causes(self):
        battle = BattleRuntime(_runtime())

        with self.assertRaisesRegex(RuntimeError, 'no source'):
            battle._validate_combat_event_contract({
                'kind': 'bot_hit', 'attacker': 1,
                'attack_reason': 0, 'death_reason': 0})
        with self.assertRaisesRegex(RuntimeError, 'does not allow kind'):
            battle._validate_combat_event_contract({
                'kind': 'health', 'attacker': 1, 'source': 'shot',
                'attack_reason': 0, 'death_reason': 0})
        with self.assertRaisesRegex(RuntimeError, 'must not have an attacker'):
            battle._validate_combat_event_contract({
                'kind': 'health', 'attacker': 1,
                'source': 'client_simulation', 'attack_reason': 0,
                'death_reason': 0})
        with self.assertRaisesRegex(RuntimeError, 'must not have an attacker'):
            battle._validate_combat_event_contract({
                'kind': 'health', 'attacker': 1,
                'source': 'player_left', 'attack_reason': None,
                'death_reason': 0})

    def test_player_left_is_nonattack_health_cause_without_feedback(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        entity = _Vehicle(11, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[11] = entity
        battle._records = {'player:2': {
            'engine_id': 11, 'state': {'health': 500, 'team': 2},
            'kind': 'player', 'network_id': 2, 'local': False,
            'presentation': True}}

        with mock.patch.object(
                critical_damage, 'apply_death', return_value=None):
            self.assertTrue(battle._apply_combat_event({
                'kind': 'health', 'target': 2, 'damage': 500,
                'health': 0, 'dead': True, 'source': 'player_left',
                'attack_reason': None, 'death_reason': 0}))

        self.assertEqual((0, 0, 0), entity.health_change)
        self.assertEqual([], battle._avatar.shot_results)
        self.assertEqual([], battle._avatar.battle_events)

    def test_disconnected_projectile_attacker_does_not_block_damage(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        target = _Vehicle(11, _Descriptor(), _Vector(0, 0, 1),
                          (0, 0, 0), {'health': 500})
        runtime.bigworld.entities[11] = target
        battle._records = {'bot:2': {
            'engine_id': 11,
            'state': {'team': 2, 'health': 500, 'alive': True},
            'kind': 'bot', 'network_id': 2, 'local': False,
            'ready': True}}
        battle._projectile_lineage.add('1:p:7:1')
        event = {
            'kind': 'bot_hit', 'attacker': 7, 'target_bot': 2,
            'projectile_id': '1:p:7:1', 'shot_seq': 1,
            'shell_index': 0, 'shot_result': 2,
            'damage': 125, 'health': 375, 'dead': False,
            'attack_reason': 0, 'death_reason': 0, 'source': 'shot',
            'world_pose': True, 'x': 0.0, 'y': 0.0, 'z': 1.0,
        }

        battle._prepare_ordered_event(event)
        self.assertTrue(battle._event_is_ready(event))
        self.assertTrue(battle._apply_combat_event(event))
        self.assertEqual((375, 0, 0), target.health_change)

        unknown = dict(event, projectile_id='1:p:7:2', health=250)
        with self.assertRaisesRegex(RuntimeError, 'unknown entity'):
            battle._prepare_ordered_event(unknown)

    def test_prebattle_freezes_input_and_publishes_battle_after_countdown(self):
        runtime = _runtime()
        runtime.bigworld.defer_vehicle_entry = True
        battle = BattleRuntime(runtime)
        client = _Client()
        start = {
            'round_id': 1, 'map': '01_karelia', 'bot_authority_id': 1,
            'players': [{
                'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                'vehicle': 'ussr:R11_MS-1', 'health': 500}],
            'bots': []}

        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player', 'prebattleCountdownSeconds': 15.0,
            'battleDurationSeconds': 900.0}, start, client))
        self.assertIs(client, runtime.compatibility.network_client)
        runtime.bigworld.callbacks.pop(0)()
        runtime.bigworld.enter_pending_vehicle(battle._server.vehicle_id)
        runtime.bigworld.callbacks.pop(0)()

        periods = [pickle.loads(zlib.decompress(payload))
                   for kind, payload in runtime.bigworld.avatar.arena_updates
                   if kind == runtime.constants.ARENA_UPDATE.PERIOD]
        self.assertEqual(
            [(2, 25.0, 15.0, [])], periods)
        self.assertEqual(
            [], runtime.bigworld.avatar.inputHandler.started_periods)
        self.assertFalse(battle._battle_live)
        battle._sender.forward = 1.0
        self.assertFalse(battle.shoot(0.0, 0.0))
        local = runtime.bigworld.entity(battle._server.vehicle_id)
        prewarm_calls = []
        battle._bots.prewarm_world_receipts = (
            lambda now: prewarm_calls.append(now) or True)

        runtime.bigworld.now = 24.9
        battle._frame()
        self.assertEqual([24.9], prewarm_calls)
        self.assertEqual([], local.teleports)
        self.assertFalse(battle._battle_live)

        runtime.bigworld.now = 25.0
        battle._frame()
        self.assertEqual([24.9, 25.0], prewarm_calls)
        self.assertTrue(battle._battle_live)
        periods = [pickle.loads(zlib.decompress(payload))
                   for kind, payload in runtime.bigworld.avatar.arena_updates
                   if kind == runtime.constants.ARENA_UPDATE.PERIOD]
        self.assertEqual((3, 925.0, 900.0, []), periods[-1])

    def test_reentrant_vehicle_enter_fails_before_roster_publication(self):
        runtime = _runtime()
        runtime.bigworld.reenter_vehicle_during_create = True
        created_avatars = []
        original_create = runtime.offline_map_creator.create

        def record_created_avatar(map_name):
            original_create(map_name)
            created_avatars.append(runtime.bigworld.avatar)

        runtime.offline_map_creator.create = record_created_avatar
        battle = BattleRuntime(runtime)
        client = _Client()
        start = {
            'round_id': 1, 'map': '01_karelia', 'bot_authority_id': 1,
            'players': [{
                'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                'vehicle': 'ussr:R11_MS-1', 'health': 500}],
            'bots': []}

        self.assertFalse(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, start, client))

        self.assertEqual('failed', battle.state)
        self.assertIn(
            'Vehicle entered before createEntity returned', battle.error)
        self.assertEqual(1, len(created_avatars))
        self.assertEqual([], created_avatars[0].arena_updates)

    def test_local_vehicle_ready_timeout_recovers_lobby(self):
        runtime = _runtime()
        runtime.bigworld.defer_vehicle_entry = True
        battle = BattleRuntime(runtime)
        client = _Client()
        start = {
            'round_id': 1, 'map': '01_karelia', 'bot_authority_id': 1,
            'players': [{
                'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                'vehicle': 'ussr:R11_MS-1', 'health': 500}],
            'bots': []}

        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player', 'startupTimeoutSeconds': 0.5}, start, client))
        runtime.bigworld.callbacks.pop(0)()
        runtime.bigworld.now = battle._vehicle_ready_deadline
        runtime.bigworld.callbacks.pop(0)()

        self.assertEqual('failed', battle.state)
        self.assertIn('did not enter world', battle.error)
        runtime.bigworld.callbacks.pop(0)()
        self.assertTrue(runtime.compatibility.account_restored)

    def test_vehicle_ready_gets_a_fresh_timeout_after_slow_map_load(self):
        runtime = _runtime()
        runtime.bigworld.defer_vehicle_entry = True
        runtime.bigworld.space_status = 0.0
        battle = BattleRuntime(runtime)
        client = _Client()
        start = {
            'round_id': 1, 'map': '01_karelia', 'bot_authority_id': 1,
            'players': [{
                'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                'vehicle': 'ussr:R11_MS-1', 'health': 500}],
            'bots': []}

        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player', 'startupTimeoutSeconds': 30.0}, start, client))
        map_deadline = battle._deadline
        runtime.bigworld.now = map_deadline - 0.1
        runtime.bigworld.callbacks.pop(0)()

        self.assertEqual('loading_entities', battle.state)
        self.assertEqual(0.0, battle._vehicle_ready_deadline)
        runtime.bigworld.space_status = 1.0
        runtime.bigworld.callbacks.pop(0)()
        self.assertGreater(battle._vehicle_ready_deadline, map_deadline)

    def test_initial_ammo_failure_does_not_leave_a_frame_callback(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()
        start = {
            'round_id': 1, 'map': '01_karelia', 'bot_authority_id': 1,
            'players': [{
                'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                'vehicle': 'ussr:R11_MS-1', 'health': 500}],
            'bots': []}

        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, start, client))
        runtime.bigworld.avatar.updateVehicleAmmo = mock.Mock(
            side_effect=RuntimeError('ammo failed'))
        runtime.bigworld.callbacks.pop(0)()

        self.assertEqual('failed', battle.state)
        self.assertIsNone(battle._callback_id)
        self.assertIsNone(battle._ammo_callback_id)
        self.assertEqual(1, len(runtime.bigworld.callbacks))
        runtime.bigworld.callbacks.pop(0)()
        self.assertEqual([], runtime.bigworld.callbacks)

    def test_gui_guard_orders_fast_page_and_ignores_late_loading(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)

        battle._install_battle_gui_guard()
        runtime.app_loader.showBattlePage()
        runtime.app_loader.showBattleLoading()

        self.assertEqual([(4, 5), (5, 6)], runtime.app_loader.transitions)
        type(runtime.app_loader).battle_loading_calls.assert_called_once_with()
        type(runtime.app_loader).battle_page_calls.assert_called_once_with()
        battle._restore_battle_gui_guard()
        self.assertIs(
            _APP_LOADER_SHOW_BATTLE_LOADING,
            type(runtime.app_loader).__dict__['showBattleLoading'])
        self.assertIs(
            _APP_LOADER_SHOW_BATTLE_PAGE,
            type(runtime.app_loader).__dict__['showBattlePage'])

    def test_gui_guard_does_not_trust_ctx_after_rejected_loading(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        type(runtime.app_loader).battle_loading_calls.return_value = False

        battle._install_battle_gui_guard()
        runtime.app_loader.showBattlePage()

        # Exact changeSpace() has already polluted __ctx.guiSpaceID, which is
        # what public getSpaceID() returns, but LobbyState rejected the change.
        self.assertEqual(5, runtime.app_loader.getSpaceID())
        self.assertEqual(4, runtime.app_loader.actual_space_id)
        self.assertEqual([(4, 5)], runtime.app_loader.transitions)
        type(runtime.app_loader).battle_page_calls.assert_not_called()

    def test_gui_guard_never_enters_loading_from_waiting(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        runtime.app_loader.space_id = 7
        runtime.app_loader.actual_space_id = 7

        with self.assertRaisesRegex(
                RuntimeError, 'not in the lobby state'):
            battle._install_battle_gui_guard()

        type(runtime.app_loader).battle_loading_calls.assert_not_called()

    def test_stale_callback_cannot_clear_a_new_generation_handle(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._generation = 1
        old_call = mock.Mock()
        new_call = mock.Mock()

        battle._schedule(0.0, old_call)
        old_wrapper = runtime.bigworld.callbacks.pop(0)
        battle._generation = 2
        battle._schedule(0.0, new_call)
        new_handle = battle._callback_id

        old_wrapper()

        self.assertEqual(new_handle, battle._callback_id)
        self.assertFalse(old_call.called)
        runtime.bigworld.callbacks.pop(0)()
        self.assertIsNone(battle._callback_id)
        new_call.assert_called_once_with()

    def test_local_vehicle_enter_failure_never_publishes_ready(self):
        runtime = _runtime()
        runtime.bigworld.defer_vehicle_entry = True
        battle = BattleRuntime(runtime)
        client = _Client()
        start = {
            'round_id': 1, 'map': '01_karelia', 'bot_authority_id': 1,
            'players': [{
                'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                'vehicle': 'ussr:R11_MS-1', 'health': 500}],
            'bots': []}

        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, start, client))
        runtime.bigworld.callbacks.pop(0)()
        vehicle_id = battle._server.vehicle_id
        avatar = battle._avatar
        battle._server.acceptVehicleEnter(vehicle_id)
        battle._server.failVehicleEnter(
            vehicle_id, RuntimeError('native enter failed'))
        runtime.bigworld.callbacks.pop(0)()

        self.assertEqual('failed', battle.state)
        self.assertIn('native enter failed', battle.error)
        self.assertFalse(any(
            update[0] == runtime.constants.ARENA_UPDATE.AVATAR_READY
            for update in avatar.arena_updates))

    def test_copied_player_physics_pose_is_published_to_lan(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()
        battle.client = client
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(2, 3, 4), (0, 0, 0),
                          {'health': 500})
        entity.speed = 7.5
        entity.filter.angularSpeed = 0.25
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=1.0, turn=0.0, handbrake=False,
            send_current=lambda: client.send_input('current'))
        battle._local_position = (2.0, 3.0, 4.0)
        battle._local_descriptor = entity.typeDescriptor
        battle._attach_local_presentation()

        battle._drive_local(0.1)

        self.assertGreater(battle._local_position[2], 4.0)
        self.assertGreater(battle._local_speed, 0.0)
        self.assertEqual(0.0, battle._local_turn_speed)
        self.assertEqual([], entity.teleports)
        self.assertIs(entity.model.matrix, battle._local_matrix)
        self.assertEqual(
            battle._local_position, tuple(entity.model.matrix.translation))
        self.assertTrue(runtime.bigworld.avatar.positions)
        self.assertTrue(client.sent)

    def test_siege_transition_locks_copied_drive_without_erasing_keys(self):
        for siege_state in (1, 3):
            runtime = _runtime()
            battle = BattleRuntime(runtime)
            battle.client = _Client()
            battle._avatar = runtime.bigworld.avatar
            descriptor = _Descriptor('sweden:S11_Strv_103B')
            descriptor.hasSiegeMode = True
            entity = _Vehicle(
                10, descriptor, _Vector(2, 3, 4), (0, 0, 0),
                {'health': 500})
            entity.siegeState = siege_state
            entity.filter.bodyMatrix = _Matrix()
            entity.filter.groundPlacingMatrix = _Matrix()
            entity.filter.groundPlacingMatrixFiltered = _Matrix()
            entity.filter.stabilisedMatrix = _Matrix()
            entity.filter.getVehiclePhysics = mock.Mock(
                side_effect=AssertionError(
                    'client-only siege touched native physics'))
            runtime.bigworld.entities[10] = entity
            battle._server = types.SimpleNamespace(vehicle_id=10)
            battle._sender = types.SimpleNamespace(
                forward=1.0, turn=1.0, aim_yaw=0.0, gun_pitch=0.0,
                handbrake=False, send_current=mock.Mock(return_value=True))
            battle._local_position = (2.0, 3.0, 4.0)
            battle._local_yaw = 0.35
            battle._local_speed = 8.0
            battle._local_turn_speed = 0.4
            battle._local_descriptor = descriptor
            battle._attach_local_presentation()

            with mock.patch(
                    'gui.mods.offline_lan_0922.battle_runtime.'
                    'vehicle_physics.longitudinal_step') as drive, \
                    mock.patch(
                        'gui.mods.offline_lan_0922.battle_runtime.'
                        'vehicle_physics.traverse_step') as traverse:
                battle._drive_local(0.1)

            drive.assert_not_called()
            traverse.assert_not_called()
            self.assertEqual((2.0, 4.0), (
                battle._local_position[0], battle._local_position[2]))
            self.assertAlmostEqual(0.35, battle._local_yaw)
            self.assertEqual((0.0, 0.0, 0.0), (
                battle._local_speed, battle._local_turn_speed,
                battle._local_drive_turn))
            self.assertEqual((1.0, 1.0), (
                battle._sender.forward, battle._sender.turn))
            entity.filter.notifyInputKeysDown.assert_called_with(0, 0)
            entity.filter.getVehiclePhysics.assert_not_called()

    def test_enabled_siege_aims_copied_hydraulic_pose_without_native_physics(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        descriptor = _Descriptor('sweden:S11_Strv_103B')
        descriptor.hasSiegeMode = True
        descriptor.type.hullAimingParams = {
            'pitch': {
                'isAvailable': True,
                'isEnabled': True,
                'wheelCorrectionCenterZ': 0.0,
                'wheelsCorrectionSpeed': 0.1,
                'wheelsCorrectionAngles': {
                    'pitchMin': -0.2,
                    'pitchMax': 0.2,
                },
            },
        }
        descriptor.gun.pitchLimits = {'absolute': (-0.07, 0.035)}
        entity = _Vehicle(
            10, descriptor, _Vector(2, 3, 4), (0, 0, 0),
            {'health': 500})
        entity.siegeState = 0
        native_body = _Matrix()
        native_ground = _Matrix()
        native_ground_filtered = _Matrix()
        native_stabilised = _Matrix()
        entity.filter.bodyMatrix = native_body
        entity.filter.groundPlacingMatrix = native_ground
        entity.filter.groundPlacingMatrixFiltered = native_ground_filtered
        entity.filter.stabilisedMatrix = native_stabilised
        entity.filter.getVehiclePhysics = mock.Mock(
            side_effect=AssertionError(
                'client-only siege touched native physics'))
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=0.0, turn=0.0, aim_yaw=0.0, gun_pitch=0.03,
            aim_pitch=-0.25,
            handbrake=False, send_current=mock.Mock(return_value=True))
        battle._local_position = (2.0, 3.0, 4.0)
        battle._local_pitch = 0.05
        battle._local_descriptor = descriptor
        battle._avatar.gunRotator.gunPitch = 0.03
        battle._attach_local_presentation()

        self.assertIs(entity.model.matrix, battle._local_pose_matrix)
        self.assertIs(
            battle._local_matrix, battle._local_pose_matrix.a)
        body_relative = battle._local_siege_body_matrix.a
        self.assertIs(native_body, body_relative.a)
        self.assertIs(native_ground, body_relative.b.source)
        self.assertIs(
            battle._local_siege_aim_world_matrix,
            battle._local_siege_body_matrix.b)
        self.assertIs(
            battle._local_siege_body_matrix,
            battle._local_siege_stabilised_matrix)
        self.assertIs(
            battle._local_siege_aim_matrix,
            battle._local_siege_aim_world_matrix.a)
        self.assertIs(
            battle._local_matrix,
            battle._local_siege_aim_world_matrix.b)

        entity.siegeState = 2
        self.assertTrue(battle._select_local_siege_pose(entity, True))
        self.assertIs(
            battle._local_siege_body_matrix,
            battle._local_pose_matrix.a)
        self.assertIs(
            battle._local_siege_body_matrix,
            battle._local_stabilised_matrix.a)
        self.assertIs(
            battle._local_siege_ground_matrix,
            battle._local_steady_rotation_matrix.a)
        self.assertTrue(battle._update_local_hull_aiming(entity, 1.0))
        self.assertAlmostEqual(-0.1, battle._local_siege_aim_pitch)
        self.assertAlmostEqual(-0.1, battle._local_siege_aim_matrix.pitch)
        entity.siegeState = 3
        descriptor.type.hullAimingParams['pitch']['isEnabled'] = False
        self.assertTrue(battle._select_local_siege_pose(entity, True))
        self.assertIs(
            battle._local_siege_body_matrix,
            battle._local_pose_matrix.a)
        self.assertFalse(battle._update_local_hull_aiming(entity, 1.0))
        self.assertAlmostEqual(0.0, battle._local_siege_aim_pitch)
        self.assertAlmostEqual(0.0, battle._local_siege_aim_matrix.pitch)
        entity.filter.getVehiclePhysics.assert_not_called()

        self.assertTrue(battle._select_local_siege_pose(entity, False))
        self.assertIs(
            battle._local_matrix, battle._local_pose_matrix.a)
        self.assertIs(
            battle._local_matrix, battle._local_stabilised_matrix.a)

    def test_close_siege_target_uses_exact_gun_axis_for_pose_marker_and_ray(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        descriptor = _Descriptor('sweden:S22_Strv_S1')
        descriptor.hasSiegeMode = True
        descriptor.type.hullAimingParams = {
            'pitch': {
                'isAvailable': True,
                'isEnabled': True,
                'wheelCorrectionCenterZ': 0.0,
                'wheelsCorrectionSpeed': 0.2,
                'wheelsCorrectionAngles': {
                    'pitchMin': math.radians(-11.0),
                    'pitchMax': math.radians(11.0),
                },
            },
        }
        descriptor.gun.pitchLimits = {'absolute': (
            math.radians(-4.0), math.radians(2.0))}
        entity = _Vehicle(
            10, descriptor, _Vector(), (0, 0, 0), {'health': 1000})
        entity.siegeState = 0
        entity.filter.bodyMatrix = _Matrix()
        entity.filter.groundPlacingMatrix = _Matrix()
        entity.filter.groundPlacingMatrixFiltered = _Matrix()
        entity.filter.stabilisedMatrix = _Matrix()
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._local_position = (0.0, 0.0, 0.0)
        battle._local_descriptor = descriptor

        distance = 43.0
        raw_pitch = math.radians(3.0)
        target_y = -math.tan(raw_pitch) * distance
        target = (0.0, target_y, distance)
        # Exact S22 data puts the gun axis 1.531358 m above and 0.351939 m
        # forward of the stabilised vehicle origin. At 43 m the omitted axis
        # offset is about two degrees, enough to cross the +2 degree gun edge.
        gun_axis_y = 0.503458 + 1.0279
        gun_axis_z = -0.008279 + 0.360218
        exact_pitch = -math.atan2(
            target_y - gun_axis_y, distance - gun_axis_z)
        expected_correction = exact_pitch - math.radians(2.0)
        approximate_correction = raw_pitch - math.radians(2.0)

        def exact_angles(actual_descriptor, matrix, angles, point):
            self.assertIs(descriptor, actual_descriptor)
            self.assertIsInstance(matrix, _Matrix)
            self.assertEqual((0.0, 0.0), angles)
            self.assertEqual(target, (point.x, point.y, point.z))
            return 0.0, exact_pitch

        runtime.get_shot_angles = mock.Mock(side_effect=exact_angles)
        battle._sender = types.SimpleNamespace(
            forward=0.0, turn=0.0, aim_yaw=0.0,
            gun_pitch=raw_pitch, aim_pitch=raw_pitch, aim_point=target,
            handbrake=False, send_current=mock.Mock(return_value=True))
        battle._attach_local_presentation()
        entity.siegeState = 2
        self.assertTrue(battle._select_local_siege_pose(entity, True))

        self.assertTrue(battle._update_local_hull_aiming(entity, 1.0))

        self.assertAlmostEqual(
            expected_correction, battle._local_siege_aim_pitch)
        self.assertAlmostEqual(
            math.radians(2.0),
            exact_pitch - battle._local_siege_aim_pitch)
        self.assertGreater(
            battle._local_siege_aim_pitch - approximate_correction,
            math.radians(2.0))
        runtime.get_shot_angles.assert_called_once()
        # CompoundModel (barrel), fixed-turret stabilisation (client marker),
        # and getCurShotPosition (fire ray) retain one hydraulic body pose.
        self.assertIs(battle._local_pose_matrix, entity.model.matrix)
        self.assertIs(
            battle._local_siege_body_matrix,
            battle._local_pose_matrix.a)
        self.assertIs(
            battle._local_siege_body_matrix,
            battle._local_stabilised_matrix.a)
        self.assertIs(
            battle._local_matrix,
            battle._local_siege_flat_body_matrix.b)
        shot_direction = _Vector(
            0.0, -math.sin(exact_pitch), math.cos(exact_pitch))
        battle._avatar.gunRotator.getCurShotPosition = mock.Mock(
            return_value=(_Vector(0.0, gun_axis_y, gun_axis_z),
                          shot_direction))
        unused_origin, ray = battle._mutable_shot_ray()
        self.assertAlmostEqual(
            exact_pitch, -math.atan2(
                ray.y, math.sqrt(ray.x * ray.x + ray.z * ray.z)))

    def test_non_siege_vehicle_never_uses_exact_hydraulic_solver(self):
        runtime = _runtime()
        runtime.get_shot_angles = mock.Mock()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._sender = types.SimpleNamespace(
            gun_pitch=0.0, aim_pitch=0.0,
            aim_point=(0.0, 0.0, 43.0))
        battle._local_siege_aim_matrix = _Matrix()
        battle._local_siege_flat_body_matrix = _Matrix()
        descriptor = _Descriptor('ussr:R11_MS-1')
        descriptor.hasSiegeMode = False
        entity = types.SimpleNamespace(typeDescriptor=descriptor)

        self.assertFalse(battle._update_local_hull_aiming(entity, 1.0))
        runtime.get_shot_angles.assert_not_called()

    def test_siege_exit_selects_plain_pose_after_descriptor_reverts(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        siege_descriptor = _Descriptor('sweden:S11_Strv_103B')
        siege_descriptor.hasSiegeMode = True
        entity = _Vehicle(
            10, siege_descriptor, _Vector(2, 3, 4), (0, 0, 0),
            {'health': 500})
        native_body = _Matrix()
        native_ground = _Matrix()
        entity.filter.bodyMatrix = native_body
        entity.filter.groundPlacingMatrix = native_ground
        entity.filter.groundPlacingMatrixFiltered = _Matrix()
        entity.filter.stabilisedMatrix = _Matrix()
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=0.0, turn=0.0, aim_yaw=0.0, gun_pitch=0.0,
            handbrake=False, send_current=mock.Mock(return_value=True))
        battle._local_position = (2.0, 3.0, 4.0)
        battle._local_descriptor = siege_descriptor
        battle._attach_local_presentation()

        self.assertTrue(battle._select_local_siege_pose(entity, True))
        self.assertIs(
            battle._local_siege_body_matrix,
            battle._local_pose_matrix.a)

        # #1513 changes the active descriptor inside its siege-state
        # callback.  The travel child is intentionally not another composite.
        entity.typeDescriptor = _Descriptor()
        entity.typeDescriptor.hasSiegeMode = False

        self.assertTrue(battle._select_local_siege_pose(entity, False))
        self.assertIs(battle._local_matrix, battle._local_pose_matrix.a)
        self.assertIs(
            battle._local_matrix, battle._local_stabilised_matrix.a)

    def test_local_siege_waits_for_native_vehicle_enter_to_finish(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        descriptor = _Descriptor('sweden:S11_Strv_103B')
        descriptor.hasSiegeMode = True
        entity = _Vehicle(
            10, descriptor, _Vector(2, 3, 4), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=0.0, turn=0.0, aim_yaw=0.0, gun_pitch=0.0,
            handbrake=False, send_current=mock.Mock(return_value=True))
        battle._local_position = (2.0, 3.0, 4.0)
        battle._local_descriptor = descriptor

        # PlayerAvatar.vehicle_onEnterWorld runs before #1513 creates these
        # four WGVehicleFilter providers. The base copied pose must still be
        # admitted at that inner callback instead of rejecting the vehicle.
        self.assertTrue(battle._prepare_local_presentation(entity))
        self.assertIsNone(battle._local_siege_body_matrix)
        self.assertIs(
            battle._local_matrix,
            runtime.compatibility.pose_overlays[id(entity)]['matrix'])

        native_body = _Matrix()
        native_ground = _Matrix()
        native_ground_filtered = _Matrix()
        native_stabilised = _Matrix()
        entity.filter.bodyMatrix = native_body
        entity.filter.groundPlacingMatrix = native_ground
        entity.filter.groundPlacingMatrixFiltered = native_ground_filtered
        entity.filter.stabilisedMatrix = native_stabilised

        self.assertTrue(battle._attach_local_presentation())
        self.assertIs(
            battle._local_pose_matrix,
            runtime.compatibility.pose_overlays[id(entity)]['matrix'])
        self.assertIs(native_stabilised,
                      battle._local_native_stabilised_matrix)
        self.assertIs(battle._local_pose_matrix, entity.model.matrix)

    def test_limited_traverse_autorotation_follows_unclamped_mouse_target(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        descriptor = _Descriptor()
        descriptor.gun.turretYawLimits = (-0.10, 0.10)
        entity = _Vehicle(
            10, descriptor, _Vector(), (0, 0, 0), {'health': 500})
        battle._sender = types.SimpleNamespace(aim_yaw=0.75)
        battle._local_yaw = 0.0
        battle._avatar.inputHandler.getAutorotation = lambda: True

        self.assertEqual(
            1.0, battle._local_autorotation_turn(entity, 0.0))
        battle._sender.aim_yaw = -0.75
        self.assertEqual(
            -1.0, battle._local_autorotation_turn(entity, 0.0))
        battle._sender.aim_yaw = 0.05
        self.assertEqual(
            0.0, battle._local_autorotation_turn(entity, 0.0))

    def test_autorotation_respects_stock_mode_and_manual_hull_input(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        descriptor = _Descriptor()
        descriptor.gun.turretYawLimits = (-0.10, 0.10)
        entity = _Vehicle(
            10, descriptor, _Vector(), (0, 0, 0), {'health': 500})
        battle._sender = types.SimpleNamespace(aim_yaw=0.75)
        battle._avatar.inputHandler.getAutorotation = lambda: False

        self.assertEqual(
            0.0, battle._local_autorotation_turn(entity, 0.0))
        battle._avatar.inputHandler.getAutorotation = lambda: True
        self.assertEqual(
            -1.0, battle._local_autorotation_turn(entity, -1.0))
        descriptor.gun.turretYawLimits = None
        self.assertEqual(
            0.0, battle._local_autorotation_turn(entity, 0.0))

    def test_limited_traverse_autorotation_requires_no_drive_or_cruise(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        descriptor = _Descriptor()
        descriptor.gun.turretYawLimits = (-0.10, 0.10)
        entity = _Vehicle(
            10, descriptor, _Vector(), (0, 0, 0), {'health': 500})
        battle._sender = types.SimpleNamespace(aim_yaw=0.75)
        battle._local_yaw = 0.0
        battle._avatar.inputHandler.getAutorotation = lambda: True

        self.assertEqual(0.0, battle._local_autorotation_turn(
            entity, 0.0, drive_intent=1.0))
        self.assertEqual(0.0, battle._local_autorotation_turn(
            entity, 0.0, drive_intent=-1.0))
        self.assertEqual(0.0, battle._local_autorotation_turn(
            entity, 0.0, drive_intent=0.25))
        self.assertEqual(1.0, battle._local_autorotation_turn(
            entity, 0.0, drive_intent=0.0))

    def test_limited_traverse_autorotation_respects_block_tracks(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        descriptor = _Descriptor()
        descriptor.gun.turretYawLimits = (-0.10, 0.10)
        entity = _Vehicle(
            10, descriptor, _Vector(), (0, 0, 0), {'health': 500})
        battle._sender = types.SimpleNamespace(aim_yaw=0.75)
        battle._local_yaw = 0.0
        battle._avatar.inputHandler.getAutorotation = lambda: True

        self.assertEqual(0.0, battle._local_autorotation_turn(
            entity, 0.0, tracks_blocked=True))
        self.assertEqual(1.0, battle._local_autorotation_turn(
            entity, 0.0, tracks_blocked=False))

    def test_limited_traverse_autorotation_uses_copied_traverse_physics(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()
        battle.client = client
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.inputHandler.getAutorotation = lambda: True
        descriptor = _Descriptor()
        descriptor.gun.turretYawLimits = (-0.10, 0.10)
        entity = _Vehicle(
            10, descriptor, _Vector(), (0, 0, 0), {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=0.0, turn=0.0, aim_yaw=0.75, handbrake=False,
            send_current=lambda: client.send_input('current'))
        battle._local_descriptor = descriptor
        battle._attach_local_presentation()

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'vehicle_physics.traverse_step', return_value=0.5) as step:
            battle._drive_local(0.1)

        self.assertEqual(1.0, step.call_args.args[2])
        self.assertAlmostEqual(0.05, battle._local_yaw)
        self.assertEqual(0.5, battle._local_turn_speed)
        self.assertEqual((2, 8), entity.engineMode)
        self.assertEqual(0.5, runtime.bigworld.avatar.positions[-1][3])

    def test_drowning_countdown_keeps_movement_until_the_vehicle_drowns(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(
            10, _Descriptor(), _Vector(2, 3, 4), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=1.0, turn=0.0, handbrake=False,
            send_current=mock.Mock(return_value=True))
        battle._local_position = (2.0, 3.0, 4.0)
        battle._local_descriptor = entity.typeDescriptor
        battle._attach_local_presentation()
        battle._drown_level = 2

        battle._drive_local(0.1)

        self.assertGreater(battle._local_position[2], 4.0)
        self.assertGreater(battle._local_speed, 0.0)
        self.assertEqual(1.0, battle._sender.forward)

        entity.isCrewActive = False
        battle._drive_local(0.1)
        self.assertEqual(0.0, battle._local_speed)
        self.assertEqual(0.0, battle._sender.forward)

    def test_player_pose_publication_preserves_thirty_hz_phase(self):
        for frame_rate in (40, 45, 50, 60, 75, 120):
            runtime = _runtime()
            battle = BattleRuntime(runtime)
            battle.client = _Client()
            battle._avatar = runtime.bigworld.avatar
            entity = _Vehicle(
                10, _Descriptor(), _Vector(2, 3, 4), (0, 0, 0),
                {'health': 500})
            runtime.bigworld.entities[10] = entity
            battle._server = types.SimpleNamespace(vehicle_id=10)
            send_current = mock.Mock(return_value=True)
            battle._sender = types.SimpleNamespace(
                forward=1.0, turn=0.0, handbrake=False,
                send_current=send_current)
            battle._local_position = (2.0, 3.0, 4.0)
            battle._local_descriptor = entity.typeDescriptor
            battle._attach_local_presentation()
            samples = []
            previous_sends = 0

            with mock.patch(
                    'gui.mods.offline_lan_0922.battle_runtime.'
                    'vehicle_physics.longitudinal_step',
                    return_value=14.0), mock.patch(
                        'gui.mods.offline_lan_0922.battle_runtime.'
                        'vehicle_physics.traverse_step', return_value=0.0):
                for unused_frame in range(frame_rate * 2):
                    battle._drive_local(1.0 / frame_rate)
                    samples.append(battle._local_position[2])
                    current_sends = send_current.call_count
                    self.assertLessEqual(current_sends - previous_sends, 1)
                    previous_sends = current_sends

            self.assertTrue(all(
                right > left for left, right in zip(samples, samples[1:])))
            self.assertEqual(frame_rate * 2, len(
                runtime.bigworld.avatar.positions))
            self.assertGreaterEqual(send_current.call_count, 59)
            self.assertLessEqual(send_current.call_count, 61)

    def test_battle_frame_requests_the_next_render_frame(self):
        self.assertEqual(0.0, FRAME_SECONDS)

    def test_optional_frame_failures_disable_features_not_the_round(self):
        runtime = _runtime()
        runtime.bigworld.now = 1.0
        battle = BattleRuntime(runtime)
        battle.client = types.SimpleNamespace()
        battle.state = 'running'
        battle._battle_live = True
        battle._last_frame_time = 0.98
        battle._avatar = runtime.bigworld.avatar
        battle._frame_diagnostics = None
        battle._maintain_standard_space_visibility = mock.Mock(
            side_effect=RuntimeError('visibility write failed'))
        battle._tick_expert_target = mock.Mock(
            side_effect=RuntimeError('Expert callback failed'))
        battle._update_target_outline = mock.Mock(
            side_effect=RuntimeError('edge callback failed'))
        battle._clear_target_outline = mock.Mock(return_value=True)
        battle._flush_pending_bot_create = mock.Mock()
        battle._flush_pending_entities = mock.Mock()
        battle._drain_event_journal = mock.Mock()
        battle._maybe_send_battle_ready = mock.Mock()
        battle._tick_critical_states = mock.Mock()
        battle._tick_drowning = mock.Mock()
        battle._tick_overturn = mock.Mock()
        battle._drive_local = mock.Mock()
        battle._report_local_compound = mock.Mock()
        battle._update_spotting = mock.Mock()
        battle._schedule = mock.Mock()
        battle._fail = mock.Mock()

        with contextlib.redirect_stdout(io.StringIO()) as log:
            battle._frame()
            battle._frame()

        self.assertEqual('running', battle.state)
        battle._fail.assert_not_called()
        self.assertEqual(2, battle._schedule.call_count)
        battle._maintain_standard_space_visibility.assert_called_once_with(
            1.0)
        battle._tick_expert_target.assert_called_once_with(1.0)
        battle._update_target_outline.assert_called_once_with(1.0)
        rendered = log.getvalue()
        for feature in (
                'map visibility filtering',
                'Expert damaged-device presentation',
                'target outline'):
            self.assertEqual(
                1, rendered.count(
                    'optional %s disabled for this round' % feature))

    def test_optional_warning_is_single_line_bounded_and_deduplicated(self):
        battle = BattleRuntime(_runtime())
        error = RuntimeError('first line\n' + 'x' * 600)

        with contextlib.redirect_stdout(io.StringIO()) as log:
            battle._warn_optional_failure('target outline', error)
            battle._warn_optional_failure('target outline', error)

        lines = log.getvalue().splitlines()
        self.assertEqual(1, len(lines))
        self.assertLessEqual(len(lines[0]), 320)
        self.assertIn('first line', lines[0])

    def test_frame_consumes_full_elapsed_time_without_truncation(self):
        runtime = _runtime()
        runtime.bigworld.now = 1.0
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._battle_live = True
        battle._last_frame_time = 0.85
        battle._avatar = runtime.bigworld.avatar
        battle._frame_diagnostics = types.SimpleNamespace(
            enabled=True, begin=mock.Mock(return_value=19),
            finish=mock.Mock())
        battle._flush_pending_bot_create = mock.Mock()
        battle._flush_pending_entities = mock.Mock()
        battle._drain_event_journal = mock.Mock()
        battle._maybe_send_battle_ready = mock.Mock()
        battle._tick_critical_states = mock.Mock()
        battle._tick_drowning = mock.Mock()
        battle._drive_local = mock.Mock()
        battle._update_target_outline = mock.Mock()
        battle._update_spotting = mock.Mock()
        battle._schedule = mock.Mock()

        battle._frame()

        battle._frame_diagnostics.begin.assert_called_once()
        self.assertAlmostEqual(
            0.15, battle._frame_diagnostics.begin.call_args[0][1])
        self.assertAlmostEqual(
            0.15, battle._tick_critical_states.call_args[0][0])
        self.assertAlmostEqual(0.15, battle._drive_local.call_args[0][0])
        finish = battle._frame_diagnostics.finish.call_args[0]
        self.assertEqual(19, finish[0])
        self.assertAlmostEqual(0.15, finish[2])
        self.assertAlmostEqual(0.15, finish[3])

    def test_countdown_transition_consumes_the_live_suffix_of_a_slow_frame(self):
        runtime = _runtime()
        runtime.bigworld.now = 1.0
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._battle_live = False
        battle._prebattle_deadline = 0.95
        battle._last_frame_time = 0.90
        battle._avatar = runtime.bigworld.avatar
        battle._flush_pending_bot_create = mock.Mock()
        battle._flush_pending_entities = mock.Mock()
        battle._drain_event_journal = mock.Mock()
        battle._maybe_send_battle_ready = mock.Mock()
        battle._tick_critical_states = mock.Mock()
        battle._tick_drowning = mock.Mock()
        battle._tick_overturn = mock.Mock()
        battle._prebattle_transition_ready = mock.Mock(return_value=True)

        def begin_battle():
            battle._battle_live = True
            battle._prebattle_deadline = None
            return True

        battle._begin_battle = mock.Mock(side_effect=begin_battle)
        battle._drive_local = mock.Mock()
        battle._update_target_outline = mock.Mock()
        battle._report_local_compound = mock.Mock()
        battle._update_spotting = mock.Mock()
        battle._schedule = mock.Mock()

        battle._frame()

        battle._begin_battle.assert_called_once_with()
        battle._tick_critical_states.assert_called_once_with(
            mock.ANY)
        self.assertAlmostEqual(
            0.05, battle._tick_critical_states.call_args[0][0])
        battle._tick_drowning.assert_called_once_with(mock.ANY, 1.0)
        self.assertAlmostEqual(
            0.05, battle._tick_drowning.call_args[0][0])
        battle._tick_overturn.assert_called_once_with(mock.ANY, 1.0)
        self.assertAlmostEqual(
            0.05, battle._tick_overturn.call_args[0][0])
        self.assertAlmostEqual(0.05, battle._drive_local.call_args[0][0])

    def test_local_physics_substeps_consume_full_slow_frame_before_return(self):
        battle = BattleRuntime(_runtime())
        battle._sender = types.SimpleNamespace(send_current=mock.Mock())
        battle._server = object()
        battle._drive_local_step = mock.Mock(return_value=False)

        battle._drive_local(0.25)

        steps = [call[0][0]
                 for call in battle._drive_local_step.call_args_list]
        self.assertEqual(3, len(steps))
        self.assertAlmostEqual(0.25, sum(steps))
        self.assertLessEqual(max(steps), 0.1)
        battle._sender.send_current.assert_called_once_with()

    def test_authority_pose_is_presented_without_a_network_publication(self):
        runtime = _runtime()
        runtime.bigworld.now = 1.0
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._battle_live = True
        battle._last_frame_time = 0.98
        battle._avatar = runtime.bigworld.avatar
        battle._last_snapshot = {'players': []}
        state = {
            'id': 17, 'x': 7.0, 'y': 2.0, 'z': 9.0,
            'yaw': 0.75, 'aim_yaw': 0.9, 'gun_pitch': -0.1,
        }
        battle._bots = types.SimpleNamespace(
            update=mock.Mock(return_value=[]),
            presentation_states=mock.Mock(return_value=(state,)))
        battle._flush_pending_bot_create = mock.Mock()
        battle._flush_pending_entities = mock.Mock()
        battle._drain_event_journal = mock.Mock()
        battle._maybe_send_battle_ready = mock.Mock()
        battle._tick_critical_states = mock.Mock()
        battle._tick_drowning = mock.Mock()
        battle._drive_local = mock.Mock()
        battle._update_target_outline = mock.Mock()
        battle._apply_authority_bot_poses = mock.Mock()
        battle._update_spotting = mock.Mock()
        battle._schedule = mock.Mock()

        battle._frame()

        update_args, update_kwargs = battle._bots.update.call_args
        self.assertAlmostEqual(0.02, update_args[0])
        self.assertEqual(1.0, update_args[1])
        self.assertEqual({'players': []}, update_kwargs)
        # The render clock is passed so a bot integrating below the
        # frame rate can be dead-reckoned for presentation.
        battle._bots.presentation_states.assert_called_once_with(1.0)
        battle._apply_authority_bot_poses.assert_called_once_with((state,))
        battle._schedule.assert_called_once_with(0.0, battle._frame)

    def test_collection_counts_report_every_round_lived_structure(self):
        # The 32-bit client dies at its address-space ceiling, so a leak has
        # to be visible as a count that grows across windows.
        battle = BattleRuntime(_runtime())
        battle._event_journal = [{'event_id': 'a'}, {'event_id': 'b'}]
        battle._accepted_event_ids = set(['a', 'b', 'c'])
        battle._applied_event_ids = set(['a'])
        battle._records = {
            'player:1': {'state': {'alive': True}},
            'bot:2': {'state': {'alive': False}},
        }
        battle._destructibles = types.SimpleNamespace(
            registry_counts=lambda: {'instances': 7, 'pending': 2})
        battle._bots = types.SimpleNamespace(states={11: {}, 12: {}})

        counts = battle._collection_counts()

        self.assertEqual(2, counts['journal'])
        self.assertEqual(3, counts['accepted_ids'])
        self.assertEqual(1, counts['applied_ids'])
        self.assertEqual(2, counts['records'])
        self.assertEqual(1, counts['records_dead'])
        self.assertEqual(7, counts['destr_instances'])
        self.assertEqual(2, counts['bot_states'])

    def test_offframe_callbacks_are_not_charged_to_engine_time(self):
        diagnostics = _FrameDiagnostics(
            writer=lambda text: None, clock=lambda: 0.0)
        diagnostics.enabled = True
        diagnostics._pending = {
            'cause': 1, 'entry_wall': 0.0, 'exec': 0.010,
            'tick_dt': 0.0, 'motion_dt': 0.0, 'stages': {}, 'probes': {},
            'context': {},
        }

        diagnostics.begin(0.100, 0.100, 0.030)

        self.assertAlmostEqual(0.030, diagnostics._offframe_sum)
        self.assertAlmostEqual(0.060, diagnostics._outside_sum)

    def test_frame_diagnostics_pull_bot_probe_counts_and_durations(self):
        runtime = _runtime()
        runtime.bigworld.now = 1.0
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._battle_live = True
        battle._last_frame_time = 0.98
        battle._avatar = runtime.bigworld.avatar
        battle._last_snapshot = {'players': []}
        diagnostics = types.SimpleNamespace(
            enabled=True, begin=mock.Mock(return_value=31),
            finish=mock.Mock())
        battle._frame_diagnostics = diagnostics
        battle._bots = types.SimpleNamespace(
            update=mock.Mock(return_value=[]),
            presentation_states=mock.Mock(return_value=()),
            probe_totals=mock.Mock(side_effect=[
                (10, 20, 30, 40, 50), (12, 23, 30, 41, 54)]),
            probe_duration_totals=mock.Mock(side_effect=[
                (0.1, 0.2, 0.3, 0.4, 0.5),
                (0.102, 0.206, 0.3, 0.404, 0.51)]),
            is_authority=mock.Mock(return_value=True))
        battle._flush_pending_bot_create = mock.Mock()
        battle._flush_pending_entities = mock.Mock()
        battle._drain_event_journal = mock.Mock()
        battle._maybe_send_battle_ready = mock.Mock()
        battle._tick_critical_states = mock.Mock()
        battle._tick_drowning = mock.Mock()
        battle._drive_local = mock.Mock()
        battle._update_target_outline = mock.Mock()
        battle._apply_authority_bot_poses = mock.Mock()
        battle._update_spotting = mock.Mock()
        battle._schedule = mock.Mock()

        battle._frame()

        finish = diagnostics.finish.call_args
        self.assertEqual(
            {'visibility': 2, 'lane': 3, 'cover': 0,
             'ground': 1, 'motion': 4},
            finish.args[5])
        durations = finish.kwargs['probe_durations']
        for name, expected in (
                ('visibility', 0.002), ('lane', 0.006),
                ('cover', 0.0), ('ground', 0.004), ('motion', 0.01)):
            self.assertAlmostEqual(expected, durations[name])

    def test_authority_observation_waits_for_the_server_relay(self):
        runtime = _runtime()
        runtime.bigworld.now = 1.0
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._battle_live = True
        battle._last_frame_time = 0.98
        battle._avatar = runtime.bigworld.avatar
        battle._last_snapshot = {'players': []}
        observation = {
            'type': 'bot_observation',
            'contacts': [{'target_kind': 'human', 'target_id': 1,
                          'observing_team': 2, 'visible': True}],
            'affordances': [],
        }
        battle._bots = types.SimpleNamespace(
            update=mock.Mock(return_value=[observation]),
            presentation_states=mock.Mock(return_value=()))
        battle._flush_pending_bot_create = mock.Mock()
        battle._flush_pending_entities = mock.Mock()
        battle._drain_event_journal = mock.Mock()
        battle._maybe_send_battle_ready = mock.Mock()
        battle._tick_critical_states = mock.Mock()
        battle._tick_drowning = mock.Mock()
        battle._drive_local = mock.Mock()
        battle._update_target_outline = mock.Mock()
        battle._apply_authority_bot_poses = mock.Mock()
        battle._update_spotting = mock.Mock()
        battle._send_bot_message = mock.Mock(return_value=True)
        battle._observe_local_vehicle = mock.Mock()
        battle._schedule = mock.Mock()

        battle._frame()

        battle._send_bot_message.assert_called_once_with(observation)
        battle._observe_local_vehicle.assert_not_called()

    def test_worker_samples_live_callbacks_without_starting_legacy_probe(self):
        runtime = _runtime()
        runtime.bigworld.now = 1.0
        battle = BattleRuntime(runtime)
        battle._worker_mode = True
        battle._config = {
            'authority_worker_probe': {'enabled': True, 'stageSeconds': 1}}
        battle.state = 'running'
        battle._battle_live = True
        battle._last_frame_time = 0.98
        battle._avatar = runtime.bigworld.avatar
        battle.client = types.SimpleNamespace(
            player_id=-1, team=1, bot_authority_id=-1,
            phase='battle', is_bot_authority=lambda: True)
        battle._last_snapshot = {'players': []}
        publication = {'type': 'bot_state', 'bots': []}
        battle._bots = types.SimpleNamespace(
            update=mock.Mock(return_value=[publication]),
            presentation_states=mock.Mock(return_value=()),
            is_authority=lambda: True,
            probe_totals=lambda: (0, 0, 0, 0, 0),
            set_camera_position=mock.Mock())
        battle._flush_pending_bot_create = mock.Mock()
        battle._flush_pending_entities = mock.Mock()
        battle._drain_event_journal = mock.Mock()
        battle._maybe_send_battle_ready = mock.Mock()
        battle._apply_authority_bot_poses = mock.Mock()
        battle._send_bot_message = mock.Mock(return_value=True)
        battle._projectile_is_authority = mock.Mock(return_value=False)
        battle._schedule = mock.Mock()

        battle._frame()

        self.assertEqual(1, battle._worker_frame_callbacks)
        self.assertEqual(
            1, battle._authority_worker_probe_sample()['frame_callbacks'])
        self.assertEqual(1, battle._worker_probe_authority_callbacks)
        self.assertEqual(1, battle._worker_probe_bot_generated)
        self.assertEqual(1, battle._worker_probe_bot_enqueued)
        self.assertEqual(0, battle._worker_probe_bot_send_failed)
        self.assertIsNone(battle._worker_probe)
        self.assertFalse(battle._worker_probe_attempted)
        battle._bots.set_camera_position.assert_called_once_with(None)

    def test_authority_replaces_local_placeholder_and_omits_remote_one(self):
        battle = BattleRuntime(_runtime())
        battle.client = _Client()
        battle._garage_loadout = {'camouflage_id': 37}
        battle._start_message = {'players': [{
            'id': 1, 'team': 1, 'slot': 0, 'health': 500,
            'alive': True}]}
        battle._local_position = (592.0, 3.0, -418.0)
        battle._local_yaw = -1.606
        battle._local_speed = 4.0
        battle._sender = _LANInputSender(battle)
        battle._sender.align_aim(0.2, -0.05)
        battle._last_snapshot = {'players': [
            {'id': 1, 'team': 1, 'slot': 0, 'participating': True,
             'world_pose': False,
             'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0},
            {'id': 2, 'team': 2, 'slot': 0, 'participating': True,
             'world_pose': False,
             'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0},
            {'id': 3, 'team': 2, 'slot': 1, 'participating': True,
             'world_pose': True,
             'x': -300.0, 'y': 2.0, 'z': 100.0, 'yaw': 1.0},
            {'id': 4, 'team': 2, 'slot': 2, 'participating': False,
             'world_pose': True,
             'x': -250.0, 'y': 2.0, 'z': 90.0, 'yaw': 1.0},
        ]}

        players = battle._authority_players()

        self.assertEqual([1, 3], [value['id'] for value in players])
        local = players[0]
        self.assertEqual((592.0, 3.0, -418.0),
                         (local['x'], local['y'], local['z']))
        self.assertAlmostEqual(-1.606, local['yaw'])
        self.assertAlmostEqual(-1.406, local['aim_yaw'])
        self.assertAlmostEqual(-0.05, local['gun_pitch'])
        self.assertEqual(37, local['camouflage_id'])
        self.assertTrue(local['world_pose'])
        self.assertFalse(
            battle._last_snapshot['players'][0]['world_pose'])

    def test_worker_authority_omits_dummy_and_unpublished_humans(self):
        battle = BattleRuntime(_runtime())
        battle._worker_mode = True
        battle.client = types.SimpleNamespace(player_id=-1)
        battle._last_snapshot = {'players': [
            {'id': -1, 'world_pose': True, 'x': 0.0, 'y': -500.0,
             'z': 0.0},
            {'id': 1, 'participating': True, 'world_pose': True,
             'x': 5.0, 'y': 0.0, 'z': 8.0},
            {'id': 2, 'participating': True, 'world_pose': False,
             'x': 0.0, 'y': 0.0, 'z': 0.0},
            {'id': 3, 'participating': False, 'world_pose': True,
             'x': 7.0, 'y': 0.0, 'z': 9.0},
            {'id': 4, 'world_pose': True,
             'x': 9.0, 'y': 0.0, 'z': 11.0},
            {'id': 0, 'world_pose': True, 'x': 1.0, 'y': 0.0, 'z': 1.0},
        ]}

        players = battle._authority_players()

        self.assertEqual([1], [value['id'] for value in players])

    def test_worker_draw_off_waits_for_pending_and_loading_models(self):
        battle = BattleRuntime(_runtime())
        battle._worker_mode = True
        battle.state = 'running'
        battle._records = {
            'player:-1': {'ready': True},
            'bot:1': {'ready': False},
        }
        battle._pending_bot_creates = {'bot:2': {}}
        battle._pending_bot_create_order = ['bot:2']

        self.assertFalse(battle.authority_worker_ready_for_draw_off())

        battle._pending_bot_creates = {}
        battle._pending_bot_create_order = []
        self.assertFalse(battle.authority_worker_ready_for_draw_off())

        battle._records['bot:1']['ready'] = True
        self.assertTrue(battle.authority_worker_ready_for_draw_off())

    def test_worker_projectile_targets_keep_real_entities_and_omit_dummy(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._worker_mode = True
        battle._local_position = (0.0, -500.0, 0.0)
        entities = {
            10: _Vehicle(10, _Descriptor(), _Vector(0.0, -500.0, 0.0),
                         (0, 0, 0), {'health': 1}),
            11: _Vehicle(11, _Descriptor(), _Vector(10.0, 0.0, 20.0),
                         (0, 0, 0), {'health': 500}),
            12: _Vehicle(12, _Descriptor(), _Vector(30.0, 0.0, 40.0),
                         (0, 0, 0), {'health': 500}),
        }
        runtime.bigworld.entities.update(entities)
        battle._records = {
            'player:-1': {
                'engine_id': 10, 'kind': 'player', 'network_id': -1,
                'ready': True, 'local': True, 'state': {}},
            'player:1': {
                'engine_id': 11, 'kind': 'player', 'network_id': 1,
                'ready': True, 'local': False, 'state': {}},
            'bot:2': {
                'engine_id': 12, 'kind': 'bot', 'network_id': 2,
                'ready': True, 'local': False, 'state': {}},
        }

        positions = battle._projectile_record_positions()

        self.assertEqual({'player:1', 'bot:2'}, set(positions))

    def test_worker_materializes_remote_compounds_without_arena_ui(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._worker_mode = True
        battle._binding = mock.Mock()
        vehicles = {
            11: _Vehicle(11, _Descriptor(), _Vector(1.0, 0.0, 2.0),
                         (0, 0, 0), {'health': 500}),
            12: _Vehicle(12, _Descriptor(), _Vector(3.0, 0.0, 4.0),
                         (0, 0, 0), {'health': 500}),
        }
        runtime.bigworld.entities.update(vehicles)
        battle._remote_factory = mock.Mock()
        battle._remote_factory.error.return_value = None
        battle._remote_factory.is_ready.return_value = True
        battle._remote_factory.get.side_effect = vehicles.get
        records = []
        for engine_id, kind, network_id in ((11, 'player', 1), (12, 'bot', 2)):
            record = {
                'engine_id': engine_id,
                'state': {'team': 1, 'health': 500, 'alive': True},
                'kind': kind, 'network_id': network_id,
                'local': False, 'presentation': True, 'ready': False,
                'arena_added': False, 'native_remote': False,
                'properties': {}, 'spot_visible': True,
            }
            records.append(record)
            self.assertTrue(battle._materialize_record(record))

        self.assertTrue(all(record['simulation_entity']
                            for record in records))
        self.assertTrue(all(record['ready'] for record in records))
        self.assertTrue(all(vehicle.model.node('HP_gunFire') is not None
                            for vehicle in vehicles.values()))
        self.assertTrue(all(battle._record_is_event_ready(record)
                            for record in records))
        battle._binding.arena_vehicle_added.assert_not_called()
        battle._binding.start_vehicle_visual.assert_not_called()

    def test_worker_death_keeps_collision_compound_without_wreck_load(self):
        runtime = _runtime()
        states = []
        original_assembler = runtime.model_assembler.prepareCompoundAssembler

        def record_assembler(descriptor, state, space, flag):
            states.append(state)
            return original_assembler(descriptor, state, space, flag)

        runtime.model_assembler.prepareCompoundAssembler = record_assembler
        descriptor = _Descriptor()
        material = types.SimpleNamespace(armor=75.0)
        descriptor.hull.hitTester.localHitTest = mock.Mock(return_value=[
            (12.0, None, 0.9, 7)])
        descriptor.hull.materials = {7: material}
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        vehicle_id = factory.create(descriptor, {
            'publicInfo': {'team': 2, 'name': 'Bot'},
            'health': 500, 'isCrewActive': True, 'gunAnglesPacked': 0},
            _Vector(3.0, 0.0, 4.0), (0.0, 0.0, 0.0))
        entity = factory.get(vehicle_id)
        compound = entity.model
        battle = BattleRuntime(runtime)
        battle._worker_mode = True
        battle._remote_factory = factory
        record = {
            'engine_id': vehicle_id, 'kind': 'bot', 'network_id': 2,
            'local': False, 'presentation': True,
            'state': {'team': 2, 'health': 500, 'alive': True},
        }
        start = _Vector(3.0, 1.0, -16.0)
        end = _Vector(3.0, 1.0, 84.0)
        before = entity.collideSegmentExt(start, end)

        with mock.patch.object(
                critical_damage, 'apply_death', return_value=None):
            battle._apply_health(record, {'health': 0, 'alive': False})

        after = entity.collideSegmentExt(start, end)
        self.assertEqual(0, entity.health)
        self.assertIs(compound, entity.model)
        self.assertEqual(['undamaged'], states)
        self.assertEqual(['vehicleHull'], [hit.compName for hit in before])
        self.assertEqual(['vehicleHull'], [hit.compName for hit in after])
        self.assertIs(material, after[0].matInfo)
        factory.destroy_all()

    def test_garage_outfit_uses_the_arena_season_and_native_vehicle(self):
        requested = []
        item = types.SimpleNamespace(getOutfit=lambda season: (
            requested.append(season) or
            types.SimpleNamespace(strCompactDescr=b'outfit:desert')))
        season_type = types.SimpleNamespace(
            fromArenaKind=lambda arena_kind: arena_kind + 100)
        components = types.ModuleType('items.components')
        constants = types.ModuleType('items.components.c11n_constants')
        constants.SeasonType = season_type
        battle = BattleRuntime(_runtime())
        battle._arena_type = types.SimpleNamespace(vehicleCamouflageKind=4)

        with mock.patch.dict(sys.modules, {
                'items.components': components,
                'items.components.c11n_constants': constants}):
            descriptor = battle._garage_outfit(item)

        self.assertEqual([104], requested)
        self.assertEqual(b'outfit:desert', descriptor)

    def test_local_compound_matrix_is_not_polled_or_rebound_per_frame(self):
        class CountingModel(object):
            def __init__(self, matrix):
                self._matrix = matrix
                self.assignments = []
                self.reads = 0

            @property
            def matrix(self):
                self.reads += 1
                return self._matrix

            @matrix.setter
            def matrix(self, value):
                self._matrix = value
                self.assignments.append(value)

        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(2, 3, 4),
                          (0, 0, 0), {'health': 500})
        entity.model = CountingModel(entity.matrix)
        entity.appearance.compoundModel = entity.model
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._local_position = (2.0, 3.0, 4.0)
        battle._local_descriptor = entity.typeDescriptor

        battle._attach_local_presentation()
        attach_count = len(entity.model.assignments)
        attach_reads = entity.model.reads
        battle._local_position = (3.0, 3.0, 5.0)
        battle._update_local_presentation(entity, 0.1)

        self.assertEqual(attach_count, len(entity.model.assignments))
        self.assertEqual(attach_reads, entity.model.reads)
        self.assertIs(battle._local_matrix, entity.model._matrix)

    def test_local_camera_motion_is_derived_from_copied_pose_each_frame(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(2, 3, 4),
                          (0, 0, 0), {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._local_position = (2.0, 3.0, 4.0)
        battle._local_descriptor = entity.typeDescriptor
        battle._attach_local_presentation()

        battle._local_position = (2.0, 3.0, 4.5)
        battle._update_local_presentation(entity, 0.1)
        overlay = runtime.compatibility.pose_overlays[id(entity)]
        self.assertEqual((0.0, 0.0, 5.0), tuple(overlay['velocity']))
        self.assertEqual((0.0, 0.0, 50.0),
                         tuple(overlay['acceleration']))

        battle._local_position = (2.0, 3.0, 5.0)
        battle._update_local_presentation(entity, 0.1)
        overlay = runtime.compatibility.pose_overlays[id(entity)]
        self.assertEqual((0.0, 0.0, 5.0), tuple(overlay['velocity']))
        self.assertEqual((0.0, 0.0, 0.0),
                         tuple(overlay['acceleration']))

        battle._sender = types.SimpleNamespace(
            forward=1.0, turn=0.0, handbrake=False,
            send_current=mock.Mock(return_value=True))
        battle._battle_result = {'winner': 1}
        battle._drive_local(0.1)
        overlay = runtime.compatibility.pose_overlays[id(entity)]
        self.assertEqual((0.0, 0.0, 0.0), tuple(overlay['velocity']))
        self.assertEqual((0.0, 0.0, -50.0),
                         tuple(overlay['acceleration']))

    def test_visible_local_motion_never_commits_destructibles(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(2, 3, 4), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=1.0, turn=0.0, handbrake=False,
            send_current=mock.Mock(return_value=True))
        battle._local_position = (2.0, 3.0, 4.0)
        battle._local_descriptor = entity.typeDescriptor
        battle._attach_local_presentation()
        battle._destructibles = mock.Mock()

        def motion_is_clear(*unused_args, **unused_kwargs):
            battle._destructibles._fell_trees_near.assert_not_called()
            return True

        battle._motion_is_clear = mock.Mock(side_effect=motion_is_clear)

        battle._drive_local(0.1)

        battle._destructibles._fell_trees_near.assert_not_called()

    def test_local_catalog_contact_blocks_static_probe_and_motion(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(2, 3, 4), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=1.0, turn=0.0, handbrake=False,
            send_current=mock.Mock(return_value=True))
        battle._local_position = (2.0, 3.0, 4.0)
        battle._local_descriptor = entity.typeDescriptor
        battle._attach_local_presentation()
        battle._destructibles = mock.Mock()
        battle._destructibles._catalog_motion_blocked.return_value = True

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'world_collision.check_horizontal_collision',
                return_value=False) as static_probe:
            battle._drive_local(0.1)

        self.assertEqual((2.0, 4.0), (
            battle._local_position[0], battle._local_position[2]))
        self.assertTrue(
            battle._destructibles._catalog_motion_blocked.called)
        self.assertTrue(static_probe.called)

    def test_catalog_motion_body_type_error_is_not_retried(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._clock = lambda: 10.0
        entity = _Vehicle(
            10, _Descriptor(), _Vector(), (0, 0, 0), {'health': 500})
        calls = []

        def resolve(*args, **kwargs):
            calls.append((args, kwargs))
            raise TypeError('sensor body failed after native work')

        battle._destructibles = types.SimpleNamespace(
            _catalog_motion_blocked=resolve,
            _catalog_pending_at_hull=mock.Mock(return_value=False))
        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'world_collision.check_horizontal_collision',
                return_value=False):
            with self.assertRaisesRegex(TypeError, 'sensor body failed'):
                battle._motion_is_clear(
                    entity, (0.0, 0.0, 0.0), 0.0, 5.0, 0.04)

        self.assertEqual(1, len(calls))

    def test_player_pending_contact_preserves_speed_but_hard_wall_damps(self):
        def exercise(pending):
            runtime = _runtime()
            battle = BattleRuntime(runtime)
            battle.client = _Client()
            battle._avatar = runtime.bigworld.avatar
            entity = _Vehicle(
                10, _Descriptor(), _Vector(2, 3, 4), (0, 0, 0),
                {'health': 500})
            runtime.bigworld.entities[10] = entity
            battle._server = types.SimpleNamespace(vehicle_id=10)
            battle._sender = types.SimpleNamespace(
                forward=1.0, turn=0.0, handbrake=False,
                send_current=mock.Mock(return_value=True))
            battle._local_position = (2.0, 3.0, 4.0)
            battle._local_descriptor = entity.typeDescriptor
            battle._attach_local_presentation()
            battle._destructibles = mock.Mock()
            battle._destructibles._catalog_pending_at_hull.return_value = (
                pending)
            battle._smoothed_drive_pitch = mock.Mock(return_value=0.0)
            battle._update_vertical_motion = mock.Mock(
                side_effect=lambda unused_entity, position, unused_yaw,
                unused_dt: position)
            battle._ground_pitch = mock.Mock(return_value=0.0)
            battle._apply_slope_slide = mock.Mock(
                side_effect=lambda position, unused_yaw, unused_dt,
                unused_entity=None: position)
            battle._resolve_local_tank_contacts = mock.Mock(
                side_effect=lambda unused_entity, position, unused_yaw,
                unused_dt: position)

            with mock.patch(
                    'gui.mods.offline_lan_0922.battle_runtime.'
                    'vehicle_physics.longitudinal_step',
                    return_value=6.0), mock.patch(
                    'gui.mods.offline_lan_0922.battle_runtime.'
                    'vehicle_physics.traverse_step',
                    return_value=0.0), mock.patch(
                    'gui.mods.offline_lan_0922.battle_runtime.'
                    'world_collision.check_horizontal_collision',
                    return_value=True) as static_probe:
                battle._drive_local(0.1)
            return battle, static_probe

        pending_battle, pending_probe = exercise(True)
        hard_battle, hard_probe = exercise(False)

        self.assertEqual((2.0, 3.0, 4.0), pending_battle._local_position)
        self.assertEqual(6.0, pending_battle._local_speed)
        self.assertEqual(1, pending_probe.call_count)
        self.assertEqual((2.0, 3.0, 4.0), hard_battle._local_position)
        self.assertEqual(0.0, hard_battle._local_speed)
        self.assertEqual(5, hard_probe.call_count)

    def test_player_hard_contact_uses_shared_second_glancing_path(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(
            10, _Descriptor(), _Vector(2, 3, 4), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=1.0, turn=0.0, handbrake=False,
            send_current=mock.Mock(return_value=True))
        battle._local_position = (2.0, 3.0, 4.0)
        battle._local_descriptor = entity.typeDescriptor
        battle._attach_local_presentation()
        battle._destructibles = mock.Mock()
        battle._smoothed_drive_pitch = mock.Mock(return_value=0.0)
        battle._motion_is_clear = mock.Mock(
            side_effect=(False, False, True))
        battle._update_vertical_motion = mock.Mock(
            side_effect=lambda unused_entity, position, unused_yaw,
            unused_dt: position)
        battle._ground_pitch = mock.Mock(return_value=0.0)
        battle._apply_slope_slide = mock.Mock(
            side_effect=lambda position, unused_yaw, unused_dt,
            unused_entity=None: position)
        battle._resolve_local_tank_contacts = mock.Mock(
            side_effect=lambda unused_entity, position, unused_yaw,
            unused_dt: position)

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'vehicle_physics.longitudinal_step', return_value=6.0), \
                mock.patch(
                    'gui.mods.offline_lan_0922.battle_runtime.'
                    'vehicle_physics.traverse_step', return_value=0.0):
            battle._drive_local(0.04)

        expected_speed, delta_x, delta_z = \
            vehicle_physics.hard_contact_step(
                6.0, 0.04, grinding=False, slide_yaw=-0.55)
        self.assertAlmostEqual(expected_speed, battle._local_speed)
        self.assertAlmostEqual(2.0 + delta_x, battle._local_position[0])
        self.assertAlmostEqual(4.0 + delta_z, battle._local_position[2])
        self.assertEqual(
            [0.0, 0.55, -0.55],
            [call.args[2] for call in battle._motion_is_clear.call_args_list])
        self.assertNotIn(
            'hull_yaw', battle._motion_is_clear.call_args_list[0].kwargs)
        self.assertTrue(all(
            call.kwargs['hull_yaw'] == 0.0 for call in
            battle._motion_is_clear.call_args_list[1:]))
        self.assertEqual(
            vehicle_physics.HARD_CONTACT_GRIND_TICKS,
            battle._local_grind)

    def test_player_pivot_cannot_swing_chassis_corners_past_arena_edge(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(
            10, _Descriptor(), _Vector(296.49, 0.0, 0.0), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=0.0, turn=-1.0, handbrake=False,
            send_current=mock.Mock(return_value=True))
        battle._arena_bounds = (-300.0, -300.0, 300.0, 300.0)
        battle._local_position = (296.49, 0.0, 0.0)
        battle._local_yaw = math.pi * 0.5
        battle._local_descriptor = entity.typeDescriptor
        battle._attach_local_presentation()
        battle._smoothed_drive_pitch = mock.Mock(return_value=0.0)
        battle._update_vertical_motion = mock.Mock(
            side_effect=lambda unused_entity, position, unused_yaw,
            unused_dt: position)
        battle._ground_pitch = mock.Mock(return_value=0.0)
        battle._apply_slope_slide = mock.Mock(
            side_effect=lambda position, unused_yaw, unused_dt,
            unused_entity=None: position)
        battle._resolve_local_tank_contacts = mock.Mock(
            side_effect=lambda unused_entity, position, unused_yaw,
            unused_dt: position)

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'vehicle_physics.longitudinal_step', return_value=0.0), \
                mock.patch(
                    'gui.mods.offline_lan_0922.battle_runtime.'
                    'vehicle_physics.traverse_step', return_value=-3.7):
            battle._drive_local(0.1)

        self.assertEqual(math.pi * 0.5, battle._local_yaw)
        self.assertEqual(0.0, battle._local_turn_speed)
        self.assertEqual((296.49, 0.0, 0.0), battle._local_position)
        self.assertEqual('arena', battle._local_motion_kinds)

    def test_ground_probe_hands_the_broken_skin_filter_to_the_engine(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        skin_filter = lambda *unused: True
        battle._destructibles = mock.Mock()
        battle._destructibles.ground_collision_filter.side_effect = (
            lambda x, z: skin_filter if x > 0.0 else None)
        calls = []

        def collide(*args):
            calls.append(args)
            return (_Vector(args[1].x, 1.0, args[1].z),
                    _Vector(0.0, 1.0, 0.0), 2)

        runtime.bigworld.wg_collideSegment = collide

        self.assertEqual(1.0, battle._ground_y(1.0, 0.0, 0.0))
        self.assertEqual(1.0, battle._ground_y(-1.0, 0.0, 0.0))
        battle._terrain_support((1.0, 0.0, 0.0), 0.0)

        self.assertEqual(5, len(calls[0]))
        self.assertIs(skin_filter, calls[0][4])
        self.assertEqual(4, len(calls[1]))
        self.assertTrue(all(len(call) == 5 for call in calls[2:]))

    def test_crushed_destructible_costs_no_speed_and_names_the_path(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._CRUSH_DIAGNOSTICS = True
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(
            10, _Descriptor(), _Vector(2, 3, 4), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=1.0, turn=0.0, handbrake=False,
            send_current=mock.Mock(return_value=True))
        battle._local_position = (2.0, 3.0, 4.0)
        battle._local_descriptor = entity.typeDescriptor
        battle._attach_local_presentation()
        battle._local_speed = 8.0
        battle._destructibles = mock.Mock()
        battle._destructibles.take_ground_skip_count.return_value = 0
        battle._destructibles._catalog_motion_blocked.return_value = {
            'status': 'crushed',
            'token': ((22, 37, None),),
            'accepted_now': True,
            'used_kinetic_speed': False,
            'kinds': 'structure',
        }
        battle._smoothed_drive_pitch = mock.Mock(return_value=0.0)
        battle._update_vertical_motion = mock.Mock(
            side_effect=lambda unused_entity, position, unused_yaw,
            unused_dt: position)
        battle._ground_pitch = mock.Mock(return_value=0.0)
        battle._apply_slope_slide = mock.Mock(
            side_effect=lambda position, unused_yaw, unused_dt,
            unused_entity=None: position)
        battle._resolve_local_tank_contacts = mock.Mock(
            side_effect=lambda unused_entity, position, unused_yaw,
            unused_dt: position)
        written = []

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'vehicle_physics.longitudinal_step', return_value=8.0), \
                mock.patch(
                    'gui.mods.offline_lan_0922.battle_runtime.'
                    'vehicle_physics.traverse_step', return_value=0.0), \
                mock.patch(
                    'gui.mods.offline_lan_0922.battle_runtime.'
                    'world_collision.check_horizontal_collision',
                    return_value='clear'), \
                mock.patch.object(sys, 'stdout') as stdout:
            stdout.write = written.append
            battle._drive_local(0.1)

        self.assertEqual(8.0, battle._local_speed)
        self.assertGreater(battle._local_position[2], 4.0)
        self.assertEqual(
            ['[Offline LAN 0.9.22] CRUSH who=local kind=structure '
             'status=crushed path=advance v0=8.00 v1=8.00 '
             'pitch=0.000 dy=+0.000 skip=0\n'],
            [line for line in written if 'CRUSH' in line])

    def test_visible_player_cap_never_requests_a_crush_commit(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(
            10, _Descriptor(), _Vector(2, 3, 4), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=1.0, turn=0.0, handbrake=False,
            send_current=mock.Mock(return_value=True))
        battle._local_position = (2.0, 3.0, 4.0)
        battle._local_descriptor = entity.typeDescriptor
        battle._attach_local_presentation()
        battle._destructibles = mock.Mock()
        battle._destructibles._catalog_motion_blocked.side_effect = ({
            'status': 'crushed',
            'token': ((22, 37, None),),
            'accepted_now': True,
            'used_kinetic_speed': True,
        }, {
            'status': 'clear', 'token': None,
            'accepted_now': False, 'used_kinetic_speed': False,
        })
        battle._smoothed_drive_pitch = mock.Mock(return_value=0.0)
        battle._update_vertical_motion = mock.Mock(
            side_effect=lambda unused_entity, position, unused_yaw,
            unused_dt: position)
        battle._ground_pitch = mock.Mock(return_value=0.0)
        battle._apply_slope_slide = mock.Mock(
            side_effect=lambda position, unused_yaw, unused_dt,
            unused_entity=None: position)
        battle._resolve_local_tank_contacts = mock.Mock(
            side_effect=lambda unused_entity, position, unused_yaw,
            unused_dt: position)

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'vehicle_physics.longitudinal_step',
                side_effect=lambda unused_params, speed, *unused_args:
                speed + 1.0), mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'vehicle_physics.traverse_step', return_value=0.0), \
                mock.patch(
                    'gui.mods.offline_lan_0922.battle_runtime.'
                    'world_collision.check_horizontal_collision',
                    side_effect=('kinetic', 'clear')) as probe:
            battle._drive_local(0.1)
            first_position = battle._local_position
            first_speed = battle._local_speed
            battle._drive_local(0.1)

        self.assertEqual((2.0, 3.0, 4.0), first_position)
        self.assertEqual(0.0, first_speed)
        self.assertEqual(1.0, battle._local_speed)
        self.assertGreater(battle._local_position[2], first_position[2])
        self.assertEqual(2, probe.call_count)
        self.assertTrue(all(call.args[-2]
                            for call in probe.call_args_list))
        self.assertTrue(all(call.args[-1] is not None
                            for call in probe.call_args_list))
        self.assertTrue(all(
            call.kwargs['commit_enabled'] is False
            for call in probe.call_args_list))
        sensor_calls = (
            battle._destructibles._catalog_motion_blocked.call_args_list)
        self.assertEqual(2, len(sensor_calls))
        self.assertTrue(all(not call.kwargs['kinetic_commit']
                            for call in sensor_calls))
        self.assertTrue(all(call.kwargs['return_detail']
                            for call in sensor_calls))

    def test_visible_player_exact_crush_proposal_advances_while_worker_commits(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._clock = lambda: 10.0
        battle._sender = types.SimpleNamespace(
            send_current=mock.Mock(return_value=True))
        battle._local_physics = _effective_params_snapshot()['physics']
        entity = _Vehicle(
            10, _Descriptor(), _Vector(), (0, 0, 0), {'health': 500})
        proposal = {
            'status': 'crushed',
            'token': ((22, 37, None),),
            'accepted_now': False,
            'used_kinetic_speed': True,
            'kinds': 'structure',
            'requires_commit': True,
        }
        battle._destructibles = types.SimpleNamespace(
            _catalog_motion_proposal=mock.Mock(return_value=proposal))

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'world_collision.check_horizontal_collision',
                return_value='kinetic') as static_probe:
            self.assertTrue(battle._motion_is_clear(
                entity, (1.0, 2.0, 3.0), 0.25, 4.0, 0.04,
                allow_crush_drive=True))
            self.assertTrue(battle._motion_is_clear(
                entity, (1.0, 2.0, 3.0), 0.25, 4.0, 0.04,
                allow_crush_drive=True))

        self.assertFalse(battle._local_motion_cap_crushed)
        self.assertEqual('kinetic', battle._local_motion_status)
        self.assertEqual([1], list(battle._local_destructible_contacts))
        self.assertEqual(
            [[22, 37, None]],
            battle._local_destructible_contacts[1]['token'])
        self.assertEqual(
            ((1.0, 2.0, 3.0), 0.25),
            battle._local_destructible_safe_poses[1])
        battle._sender.send_current.assert_called_once_with()
        self.assertEqual(2, static_probe.call_count)
        self.assertTrue(all(
            call.kwargs['commit_enabled'] is False
            for call in static_probe.call_args_list))

    def test_visible_crush_prediction_precedes_the_static_recast(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._clock = lambda: 10.0
        battle._sender = types.SimpleNamespace(
            send_current=mock.Mock(return_value=True))
        battle._local_physics = _effective_params_snapshot()['physics']
        entity = _Vehicle(
            10, _Descriptor(), _Vector(), (0, 0, 0), {'health': 500})
        predicted = []
        battle._destructibles = types.SimpleNamespace(
            _catalog_motion_proposal=mock.Mock(return_value={
                'status': 'crushed', 'token': ((22, 37, None),),
                'accepted_now': False, 'used_kinetic_speed': True,
                'kinds': 'fragile', 'requires_commit': True,
            }),
            commit_local_prediction=lambda unused_space, token,
                unused_position, unused_yaw, unused_speed: (
                    predicted.append(token) or True),
            clear_local_prediction=mock.Mock(return_value=True))

        def static_recast(*unused_args, **unused_kwargs):
            self.assertEqual([((22, 37, None),)], predicted)
            return 'clear'

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'world_collision.check_horizontal_collision',
                side_effect=static_recast):
            self.assertTrue(battle._motion_is_clear(
                entity, (1.0, 2.0, 3.0), 0.25, 4.0, 0.04,
                allow_crush_drive=True))

        self.assertEqual([1], list(battle._local_destructible_contacts))
        battle._destructibles.clear_local_prediction.assert_not_called()

    def test_visible_crush_proposal_keeps_a_backing_wall_hard(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._clock = lambda: 10.0
        battle._sender = types.SimpleNamespace(
            send_current=mock.Mock(return_value=True))
        battle._local_physics = _effective_params_snapshot()['physics']
        entity = _Vehicle(
            10, _Descriptor(), _Vector(), (0, 0, 0), {'health': 500})
        clear_prediction = mock.Mock(return_value=True)
        battle._destructibles = types.SimpleNamespace(
            _catalog_motion_proposal=mock.Mock(return_value={
                'status': 'crushed',
                'token': ((22, 37, None),),
                'accepted_now': False,
                'used_kinetic_speed': True,
                'kinds': 'structure',
                'requires_commit': True,
            }),
            commit_local_prediction=mock.Mock(return_value=True),
            clear_local_prediction=clear_prediction)

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'world_collision.check_horizontal_collision',
                return_value='hard') as static_probe:
            self.assertFalse(battle._motion_is_clear(
                entity, (1.0, 2.0, 3.0), 0.25, 4.0, 0.04,
                allow_crush_drive=True))

        self.assertEqual([], list(battle._local_destructible_contacts))
        self.assertEqual(0, battle._local_destructible_contact_seq)
        battle._sender.send_current.assert_not_called()
        self.assertEqual('hard', battle._local_motion_status)
        self.assertFalse(static_probe.call_args.kwargs['commit_enabled'])
        clear_prediction.assert_called_once_with(((22, 37, None),))

    def test_drive_sends_new_destructible_before_advancing_safe_pose(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(
            10, _Descriptor(), _Vector(2, 3, 4), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        sent = []

        def send_current():
            sent.append((
                battle.local_pose(),
                copy.deepcopy(battle.local_destructible_contacts())))
            return True

        battle._sender = types.SimpleNamespace(
            forward=1.0, turn=0.0, handbrake=False,
            send_current=mock.Mock(side_effect=send_current))
        battle._local_position = (2.0, 3.0, 4.0)
        battle._local_descriptor = entity.typeDescriptor
        battle._attach_local_presentation()
        battle._destructibles = mock.Mock()
        battle._destructibles.take_ground_skip_count.return_value = 0
        battle._destructibles._catalog_motion_proposal.return_value = {
            'status': 'crushed',
            'token': ((22, 37, None),),
            'accepted_now': False,
            'used_kinetic_speed': True,
            'kinds': 'structure',
            'requires_commit': True,
        }
        battle._smoothed_drive_pitch = mock.Mock(return_value=0.0)
        battle._update_vertical_motion = mock.Mock(
            side_effect=lambda unused_entity, position, unused_yaw,
            unused_dt: position)
        battle._ground_pitch = mock.Mock(return_value=0.0)
        battle._apply_slope_slide = mock.Mock(
            side_effect=lambda position, unused_yaw, unused_dt,
            unused_entity=None: position)
        battle._resolve_local_tank_contacts = mock.Mock(
            side_effect=lambda unused_entity, position, unused_yaw,
            unused_dt: position)

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'vehicle_physics.longitudinal_step', return_value=8.0), \
                mock.patch(
                    'gui.mods.offline_lan_0922.battle_runtime.'
                    'vehicle_physics.traverse_step', return_value=0.0):
            battle._drive_local(0.01)
            first_position = battle._local_position
            battle._drive_local(0.01)

        self.assertEqual(1, battle._sender.send_current.call_count)
        self.assertEqual(((2.0, 3.0, 4.0), 0.0), sent[0][0])
        self.assertEqual((2.0, 3.0, 4.0), (
            sent[0][1][0]['x'], sent[0][1][0]['y'],
            sent[0][1][0]['z']))
        self.assertGreater(first_position[2], 4.0)
        self.assertGreater(battle._local_position[2], first_position[2])
        self.assertEqual(0.01, battle._input_accumulator)

    def test_destructible_pre_advance_send_failure_fails_closed(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._sender = types.SimpleNamespace(
            send_current=mock.Mock(return_value=False))
        battle._local_physics = _effective_params_snapshot()['physics']
        entity = _Vehicle(
            10, _Descriptor(), _Vector(), (0, 0, 0), {'health': 500})
        battle._destructibles = types.SimpleNamespace(
            _catalog_motion_proposal=mock.Mock(return_value={
                'status': 'crushed',
                'token': ((22, 37, None),),
                'accepted_now': False,
                'used_kinetic_speed': True,
                'kinds': 'structure',
                'requires_commit': True,
            }))

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'vehicle_physics.derive_params', return_value={
                    'speedFwd': 20.0, 'speedBwd': 8.0}):
            self.assertFalse(battle._motion_is_clear(
                entity, (1.0, 2.0, 3.0), 0.25, 4.0, 0.04,
                allow_crush_drive=True))

        self.assertEqual(0, battle._local_destructible_contact_seq)
        self.assertEqual([], list(battle._local_destructible_contacts))
        self.assertEqual([], list(battle._local_destructible_safe_poses))
        battle._sender.send_current.assert_called_once_with()

    def test_destructible_snapshot_rejection_keeps_visible_native_crush(self):
        battle = BattleRuntime(_runtime())
        battle.client = _Client()
        clear_prediction = mock.Mock(return_value=True)
        battle._destructibles = types.SimpleNamespace(
            clear_local_prediction=clear_prediction)
        detail = {
            'requires_commit': True,
            'token': ((22, 37, None),),
        }
        self.assertTrue(battle._queue_local_destructible_contact(
            detail, (1.12567, 2.0, 3.0), 0.25, 4.0, 0.04))
        self.assertTrue(battle._queue_local_destructible_contact(
            dict(detail, token=((22, 38, None),)),
            (5.0, 2.0, 3.0), 0.5, 4.0, 0.04))
        battle._local_position = (9.0, 2.0, 3.0)
        battle._local_yaw = 1.0
        battle._local_speed = 8.0
        battle._local_vertical_speed = -3.0
        battle._local_turn_speed = 0.4
        battle._local_push_x = 2.0
        battle._local_push_z = -2.0

        self.assertTrue(battle._ack_local_destructible_contacts({
            'players': [{
                'id': 1, 'destructible_contact_resolved_seq': 2,
                'destructible_contact_rejected_seqs': [2, 1],
            }],
        }))

        self.assertEqual((9.0, 2.0, 3.0), battle._local_position)
        self.assertEqual(1.0, battle._local_yaw)
        self.assertEqual(
            (8.0, -3.0, 0.4, 2.0, -2.0),
            (battle._local_speed, battle._local_vertical_speed,
             battle._local_turn_speed, battle._local_push_x,
             battle._local_push_z))
        self.assertEqual([], list(battle._local_destructible_contacts))
        self.assertEqual([], list(battle._local_destructible_safe_poses))
        self.assertEqual([
            mock.call(((22, 37, None),)),
            mock.call(((22, 38, None),)),
        ], clear_prediction.call_args_list)

    def test_direct_destructible_rejection_never_rewinds_visible_pose(self):
        battle = BattleRuntime(_runtime())
        battle.client = _Client()
        battle._start_message = {'round_id': 7}
        detail = {
            'requires_commit': True,
            'token': ((22, 37, None),),
        }
        self.assertTrue(battle._queue_local_destructible_contact(
            detail, (1.0, 2.0, 3.0), 0.25, 4.0, 0.04))
        self.assertTrue(battle._queue_local_destructible_contact(
            dict(detail, token=((22, 38, None),)),
            (5.0, 2.0, 3.0), 0.5, 4.0, 0.04))
        battle._local_position = (9.0, 2.0, 3.0)
        battle._local_speed = 8.0

        self.assertTrue(battle.on_player_destructible_contact_result({
            'type': 'player_destructible_contact_result',
            'round_id': 7, 'contact_seq': 1, 'accepted': False,
            'x': 1.125, 'y': 2.25, 'z': 3.5, 'yaw': 0.3,
        }))
        self.assertEqual((9.0, 2.0, 3.0), battle._local_position)
        self.assertEqual(8.0, battle._local_speed)
        self.assertEqual([2], list(battle._local_destructible_contacts))
        self.assertEqual([], list(battle._local_destructible_safe_poses))

        # A later disagreement only terminates its ordered row as well.
        self.assertTrue(battle.on_player_destructible_contact_result({
            'type': 'player_destructible_contact_result',
            'round_id': 7, 'contact_seq': 2, 'accepted': False,
        }))
        self.assertEqual((9.0, 2.0, 3.0), battle._local_position)
        self.assertFalse(battle.on_player_destructible_contact_result({
            'type': 'player_destructible_contact_result',
            'round_id': 7, 'contact_seq': 1, 'accepted': False,
            'x': 20.0, 'y': 20.0, 'z': 20.0, 'yaw': 1.0,
        }))
        self.assertEqual((9.0, 2.0, 3.0), battle._local_position)

    def test_neutral_coast_does_not_request_kinetic_crush_drive(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(
            10, _Descriptor(), _Vector(2, 3, 4), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=0.0, turn=0.0, handbrake=False,
            send_current=mock.Mock(return_value=True))
        battle._local_position = (2.0, 3.0, 4.0)
        battle._local_descriptor = entity.typeDescriptor
        battle._attach_local_presentation()
        battle._local_speed = 4.0
        battle._destructibles = mock.Mock()
        battle._smoothed_drive_pitch = mock.Mock(return_value=0.0)
        battle._update_vertical_motion = mock.Mock(
            side_effect=lambda unused_entity, position, unused_yaw,
            unused_dt: position)
        battle._ground_pitch = mock.Mock(return_value=0.0)
        battle._apply_slope_slide = mock.Mock(
            side_effect=lambda position, unused_yaw, unused_dt,
            unused_entity=None: position)
        battle._resolve_local_tank_contacts = mock.Mock(
            side_effect=lambda unused_entity, position, unused_yaw,
            unused_dt: position)

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'vehicle_physics.longitudinal_step', return_value=4.0), \
                mock.patch(
                    'gui.mods.offline_lan_0922.battle_runtime.'
                    'vehicle_physics.traverse_step', return_value=0.0), \
                mock.patch(
                    'gui.mods.offline_lan_0922.battle_runtime.'
                    'world_collision.check_horizontal_collision',
                    return_value='hard') as probe:
            battle._drive_local(0.1)

        self.assertTrue(probe.called)
        self.assertTrue(all(not call.args[-2]
                            for call in probe.call_args_list))
        self.assertTrue(all(call.args[-1] is None
                            for call in probe.call_args_list))
        self.assertFalse(battle._local_motion_cap_crushed)
        battle._destructibles._catalog_motion_blocked.assert_not_called()

    def test_static_contact_short_circuits_ambiguous_catalog_layer(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._destructibles = mock.Mock()
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'world_collision.check_horizontal_collision',
                return_value=True):
            self.assertFalse(battle._motion_is_clear(
                entity, (0.0, 0.0, 0.0), 0.0, 5.0, 0.04))

        battle._destructibles._catalog_motion_blocked.assert_not_called()

    def test_world_contact_without_catalog_never_becomes_clear(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._destructibles = None
        battle._local_physics = _effective_params_snapshot()['physics']
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})

        for world_status in ('hard', 'kinetic'):
            with self.subTest(world_status=world_status), mock.patch(
                    'gui.mods.offline_lan_0922.battle_runtime.'
                    'world_collision.check_horizontal_collision',
                    return_value=world_status):
                self.assertFalse(battle._motion_is_clear(
                    entity, (0.0, 0.0, 0.0), 0.0, 1.0, 0.04,
                    allow_crush_drive=True))

    def test_player_reverse_braking_does_not_enable_cap_crush(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(
            10, _Descriptor(), _Vector(2, 3, 4), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=-1.0, turn=0.0, handbrake=False,
            send_current=mock.Mock(return_value=True))
        battle._local_position = (2.0, 3.0, 4.0)
        battle._local_descriptor = entity.typeDescriptor
        battle._attach_local_presentation()
        battle._local_speed = 4.0
        battle._smoothed_drive_pitch = mock.Mock(return_value=0.0)
        battle._motion_is_clear = mock.Mock(return_value=True)
        battle._update_vertical_motion = mock.Mock(
            side_effect=lambda unused_entity, position, unused_yaw,
            unused_dt: position)
        battle._ground_pitch = mock.Mock(return_value=0.0)
        battle._apply_slope_slide = mock.Mock(
            side_effect=lambda position, unused_yaw, unused_dt,
            unused_entity=None: position)
        battle._resolve_local_tank_contacts = mock.Mock(
            side_effect=lambda unused_entity, position, unused_yaw,
            unused_dt: position)

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'vehicle_physics.longitudinal_step', return_value=3.0), \
                mock.patch(
                    'gui.mods.offline_lan_0922.battle_runtime.'
                    'vehicle_physics.traverse_step', return_value=0.0):
            battle._drive_local(0.1)

        battle._motion_is_clear.assert_called_once()
        self.assertFalse(
            battle._motion_is_clear.call_args.kwargs['allow_crush_drive'])

    def test_bot_braking_opposite_motion_does_not_enable_cap_crush(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._bots = types.SimpleNamespace(states={
            11: {'movement_dir': -1, 'airborne': False},
        })
        battle._destructibles = mock.Mock()
        battle._destructibles._catalog_pending_at_hull.return_value = False
        battle._destructibles._catalog_motion_blocked.return_value = {
            'status': 'clear', 'token': None,
            'accepted_now': False, 'used_kinetic_speed': False,
        }
        descriptor = _Descriptor()

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'world_collision.check_horizontal_collision',
                return_value='clear') as world_probe:
            status = battle._resolve_bot_motion(
                11, (0.0, 0.0, 0.0), 0.0, 4.0,
                descriptor, 0.04, 10.0)

        self.assertEqual('clear', status)
        self.assertFalse(world_probe.call_args.args[-2])
        self.assertIsNone(world_probe.call_args.args[-1])
        sensor_call = (
            battle._destructibles._catalog_motion_blocked.call_args)
        self.assertFalse(sensor_call.kwargs['kinetic_commit'])
        self.assertIsNone(sensor_call.kwargs['kinetic_speed'])

    def test_bot_motion_read_only_probe_disables_every_commit_path(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._bots = types.SimpleNamespace(states={
            11: {'movement_dir': 1, 'airborne': False},
        })
        battle._destructibles = mock.Mock()
        battle._destructibles._catalog_motion_blocked.return_value = {
            'status': 'clear', 'token': None,
            'accepted_now': False, 'used_kinetic_speed': False,
        }

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'world_collision.check_horizontal_collision',
                return_value='kinetic') as world_probe:
            status = battle._resolve_bot_motion(
                11, (0.0, 0.0, 0.0), 0.0, 4.0,
                _Descriptor(), 0.04, 10.0, commit_enabled=False)

        self.assertEqual('clear', status)
        self.assertFalse(world_probe.call_args.kwargs['commit_enabled'])
        sensor_call = (
            battle._destructibles._catalog_motion_blocked.call_args)
        self.assertFalse(sensor_call.kwargs['commit_enabled'])
        self.assertFalse(sensor_call.kwargs['kinetic_commit'])

    def test_bot_clear_catalog_guard_skips_per_frame_world_probe(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._bots = types.SimpleNamespace(states={
            11: {'movement_dir': 1, 'airborne': False},
        }, motion_world_receipt_reusable=mock.Mock(return_value=True))
        battle._destructibles = mock.Mock()
        battle._destructibles._catalog_hull_contact.return_value = False

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'world_collision.check_horizontal_collision') as world_probe:
            status = battle._resolve_bot_motion(
                11, (0.0, 0.0, 0.0), 0.0, 4.0,
                _Descriptor(), 0.04, 10.0)

        self.assertEqual('clear', status)
        world_probe.assert_not_called()
        battle._destructibles._catalog_motion_blocked.assert_not_called()
        battle._bots.motion_world_receipt_reusable.assert_called_once_with(
            11, (0.0, 0.0, 0.0), 0.0, 4.0, 10.0, 0.04)

    def test_bot_pending_exact_receipt_reuses_generic_world_corridor(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        corridor_reusable = mock.Mock(return_value=True)
        exact_reusable = mock.Mock(return_value=False)
        battle._bots = types.SimpleNamespace(
            states={11: {
                'movement_dir': 1, 'rotation_dir': 0,
                'airborne': False,
            }},
            motion_world_corridor_reusable=corridor_reusable,
            motion_world_receipt_reusable=exact_reusable)
        battle._destructibles = mock.Mock()
        battle._destructibles._catalog_hull_contact.return_value = False

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'world_collision.check_horizontal_collision') as world_probe:
            status = battle._resolve_bot_motion(
                11, (0.0, 0.0, 0.0), 0.0, 4.0,
                _Descriptor(), 0.04, 10.0)

        self.assertEqual('clear', status)
        corridor_reusable.assert_called_once_with(
            11, (0.0, 0.0, 0.0), 0.0, 4.0, 10.0, 0.04)
        exact_reusable.assert_not_called()
        world_probe.assert_not_called()
        battle._destructibles._catalog_motion_blocked.assert_not_called()

    def test_bot_pending_generic_corridor_recasts_before_hull_leaves_proof(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        bots = bot_runtime.BotRuntime(1)
        bots.states = {11: {
            'movement_dir': 1, 'rotation_dir': 0,
            'airborne': False,
        }}
        bots._motion_probe_cache[11] = {
            'result': {
                'clear': True, 'collision': False, 'slope': 0.0,
                '_world_receipt_pending': True,
            },
            'position': (0.0, 0.0, 0.0),
            'yaw': 0.0,
            'probe_distance': 15.0,
            'probe_leading': 3.5,
            'deadline': 0.0,
        }
        battle._bots = bots
        battle._destructibles = mock.Mock()
        battle._destructibles._catalog_hull_contact.return_value = False
        battle._destructibles._catalog_motion_blocked.return_value = {
            'status': 'clear', 'token': None,
            'accepted_now': False, 'used_kinetic_speed': False,
        }

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'world_collision.check_horizontal_collision',
                return_value='clear') as world_probe:
            inside = battle._resolve_bot_motion(
                11, (0.0, 0.0, 11.0), 0.0, 4.0,
                _Descriptor(), 0.04, 10.0)
            self.assertEqual('clear', inside)
            world_probe.assert_not_called()

            outside = battle._resolve_bot_motion(
                11, (0.0, 0.0, 11.2), 0.0, 4.0,
                _Descriptor(), 0.04, 10.0)

        self.assertEqual('clear', outside)
        world_probe.assert_called_once()
        battle._destructibles._catalog_motion_blocked.assert_called_once()

    def test_bot_reverse_receipt_uses_travel_yaw_and_actual_dt(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        receipt_reusable = mock.Mock(return_value=True)
        battle._bots = types.SimpleNamespace(
            states={11: {
                'movement_dir': -1, 'rotation_dir': 0,
                'airborne': False,
            }},
            motion_world_receipt_reusable=receipt_reusable)
        battle._destructibles = mock.Mock()
        battle._destructibles._catalog_hull_contact.return_value = False

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'world_collision.check_horizontal_collision') as world_probe:
            status = battle._resolve_bot_motion(
                11, (0.0, 0.0, 0.0), 0.0, -4.0,
                _Descriptor(), 0.04, 10.0)

        self.assertEqual('clear', status)
        receipt_reusable.assert_called_once_with(
            11, (0.0, 0.0, 0.0), math.pi, -4.0, 10.0, 0.04)
        world_probe.assert_not_called()
        battle._destructibles._catalog_motion_blocked.assert_not_called()

    def test_bot_coast_without_direction_corridor_keeps_world_probe(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._bots = types.SimpleNamespace(states={
            11: {'movement_dir': 0, 'rotation_dir': 0, 'airborne': False},
        })
        battle._destructibles = mock.Mock()
        battle._destructibles._catalog_pending_at_hull.return_value = False

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'world_collision.check_horizontal_collision',
                return_value='hard') as world_probe:
            status = battle._resolve_bot_motion(
                11, (0.0, 0.0, 0.0), 0.0, 4.0,
                _Descriptor(), 0.04, 10.0)

        self.assertEqual('hard', status)
        world_probe.assert_called_once()
        battle._destructibles._catalog_hull_contact.assert_not_called()
        battle._destructibles._catalog_motion_blocked.assert_not_called()

    def test_bot_residual_turn_speed_keeps_world_probe(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._bots = types.SimpleNamespace(
            states={11: {'movement_dir': 1, 'rotation_dir': 0,
                         'airborne': False}},
            _turn_speeds={11: 0.5})
        battle._destructibles = mock.Mock()
        battle._destructibles._catalog_pending_at_hull.return_value = False

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'world_collision.check_horizontal_collision',
                return_value='hard') as world_probe:
            status = battle._resolve_bot_motion(
                11, (0.0, 0.0, 0.0), 0.0, 4.0,
                _Descriptor(), 0.04, 10.0)

        self.assertEqual('hard', status)
        world_probe.assert_called_once()
        battle._destructibles._catalog_hull_contact.assert_not_called()
        battle._destructibles._catalog_motion_blocked.assert_not_called()

    def test_bot_airborne_motion_uses_airborne_world_probe_without_commit(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._bots = types.SimpleNamespace(states={
            11: {'movement_dir': 1, 'airborne': True},
        })
        battle._destructibles = mock.Mock()

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'world_collision.check_horizontal_collision',
                return_value='clear') as world_probe:
            status = battle._resolve_bot_motion(
                11, (0.0, 2.0, 0.0), 0.0, 4.0,
                _Descriptor(), 0.04, 10.0)

        self.assertEqual('clear', status)
        self.assertTrue(world_probe.call_args.args[7])
        self.assertFalse(world_probe.call_args.args[-2])
        self.assertIsNone(world_probe.call_args.args[-1])
        battle._destructibles._catalog_hull_contact.assert_not_called()
        battle._destructibles._catalog_motion_blocked.assert_not_called()

    def test_hard_compound_contact_overrides_accepted_cap_receipt(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._bots = types.SimpleNamespace(states={
            11: {'movement_dir': 1, 'airborne': False},
        })
        battle._destructibles = mock.Mock()
        battle._destructibles._catalog_hull_contact.return_value = True
        battle._destructibles._catalog_motion_blocked.return_value = {
            'status': 'hard', 'token': None,
            'accepted_now': True, 'used_kinetic_speed': True,
        }

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'world_collision.check_horizontal_collision',
                return_value='clear'):
            status = battle._resolve_bot_motion(
                11, (0.0, 0.0, 0.0), 0.0, 4.0,
                _Descriptor(), 0.04, 10.0)

        self.assertEqual('hard', status)

    def test_destroyed_track_locks_drive_and_turn_through_brake_path(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        entity.is_tracked = True
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=1.0, turn=1.0, handbrake=False,
            send_current=mock.Mock(return_value=True))
        battle._local_descriptor = entity.typeDescriptor
        battle._attach_local_presentation()
        battle._local_speed = 6.0
        battle._local_turn_speed = 0.7

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'vehicle_physics.longitudinal_step', return_value=0.0) as drive, \
                mock.patch(
                    'gui.mods.offline_lan_0922.battle_runtime.'
                    'vehicle_physics.traverse_step', return_value=0.0) as turn:
            battle._drive_local(0.1)

        self.assertEqual(0.0, drive.call_args[0][2])
        self.assertTrue(drive.call_args[0][8])
        self.assertEqual(0.0, turn.call_args[0][2])
        self.assertEqual(0.0, turn.call_args[0][1])
        self.assertEqual(0.0, turn.call_args.kwargs['drive_intent'])

    def test_siege_request_locks_drive_until_ack_but_keeps_world_physics(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        descriptor = _Descriptor('sweden:S21_UDES_03')
        descriptor.hasSiegeMode = True
        entity = _Vehicle(10, descriptor, _Vector(2, 3, 4), (0, 0, 0),
                          {'health': 500})
        entity.siegeState = runtime.constants.VEHICLE_SIEGE_STATE.DISABLED
        entity.filter.bodyMatrix = _Matrix()
        entity.filter.groundPlacingMatrix = _Matrix()
        entity.filter.groundPlacingMatrixFiltered = _Matrix()
        entity.filter.stabilisedMatrix = _Matrix()
        entity.filter.getVehiclePhysics = lambda: types.SimpleNamespace(
            setHullAimingAnglesDelta=mock.Mock())
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = _LANInputSender(battle)
        battle._sender.forward = 1.0
        battle._sender.turn = 1.0
        battle._local_position = (2.0, 3.0, 4.0)
        battle._local_descriptor = descriptor
        battle._attach_local_presentation()
        setting = runtime.constants.VEHICLE_SETTING.SIEGE_MODE_ENABLED

        self.assertTrue(battle.change_vehicle_setting(setting, True))
        self.assertEqual((True, 1), battle._local_siege_pending)
        self.assertEqual((1.0, 1.0), (
            battle._sender.forward, battle._sender.turn))

        with mock.patch.object(
                battle, '_update_vertical_motion',
                return_value=(2.0, 2.5, 4.0)) as vertical, \
                mock.patch.object(
                    battle, '_ground_pitch', return_value=0.0), \
                mock.patch.object(
                    battle, '_apply_slope_slide',
                    return_value=(2.5, 2.5, 4.0)) as slope, \
                mock.patch.object(
                    battle, '_resolve_local_tank_contacts',
                    return_value=(2.5, 2.5, 4.5)) as contacts, \
                mock.patch.object(
                    vehicle_physics, 'longitudinal_step') as drive, \
                mock.patch.object(
                    vehicle_physics, 'traverse_step') as traverse:
            battle._drive_local(0.1)

        drive.assert_not_called()
        traverse.assert_not_called()
        vertical.assert_called_once()
        slope.assert_called_once()
        contacts.assert_called_once()
        self.assertEqual((2.5, 2.5, 4.5), battle._local_position)
        self.assertEqual((0.0, 0.0), (
            battle._local_speed, battle._local_turn_speed))
        self.assertEqual((1.0, 1.0), (
            battle._sender.forward, battle._sender.turn))

        def update_siege(unused_id, state, unused_time_left):
            entity.siegeState = state
            return True

        battle._binding = types.SimpleNamespace(
            update_vehicle_siege_state=mock.Mock(
                side_effect=update_siege))
        record = {'engine_id': 10, 'local': True}
        self.assertFalse(battle._apply_siege_state(record, {
            'input_seq': 0, 'siege_state': 0,
            'siege_time_left_ms': 0}))
        self.assertEqual((True, 1), battle._local_siege_pending)
        self.assertTrue(battle._apply_siege_state(record, {
            'input_seq': 1, 'siege_state': 1,
            'siege_time_left_ms': 2000}))
        self.assertEqual((True, 1), battle._local_siege_pending)
        self.assertTrue(battle._apply_siege_state(record, {
            'input_seq': 1, 'siege_state': 2,
            'siege_time_left_ms': 0}))
        self.assertIsNone(battle._local_siege_pending)

        with mock.patch.object(
                battle, '_motion_is_clear', return_value=True), \
                mock.patch.object(
                    battle, '_update_vertical_motion',
                    side_effect=lambda unused_entity, position,
                    unused_yaw, unused_dt: position), \
                mock.patch.object(
                    battle, '_ground_pitch', return_value=0.0), \
                mock.patch.object(
                    battle, '_apply_slope_slide',
                    side_effect=lambda position, unused_yaw, unused_dt,
                    unused_entity=None: position), \
                mock.patch.object(
                    battle, '_resolve_local_tank_contacts',
                    side_effect=lambda unused_entity, position,
                    unused_yaw, unused_dt: position), \
                mock.patch.object(
                    vehicle_physics, 'longitudinal_step',
                    return_value=2.0) as drive, \
                mock.patch.object(
                    vehicle_physics, 'traverse_step',
                    return_value=1.0) as traverse:
            battle._drive_local(0.1)

        self.assertEqual(1.0, drive.call_args[0][2])
        self.assertEqual(1.0, traverse.call_args[0][2])
        self.assertEqual(1.0, traverse.call_args.kwargs['drive_intent'])

    def test_yellow_tracks_keep_full_local_drive_and_traverse(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        entity.devices_hp = {
            'leftTrackHealth': 100.0,
            'rightTrackHealth': 100.0,
        }
        entity._destroyed_devices = set()
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=1.0, turn=1.0, handbrake=False,
            send_current=mock.Mock(return_value=True))
        battle._local_descriptor = entity.typeDescriptor
        battle._attach_local_presentation()

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'vehicle_physics.longitudinal_step', return_value=0.0) as drive, \
                mock.patch(
                    'gui.mods.offline_lan_0922.battle_runtime.'
                    'vehicle_physics.traverse_step', return_value=2.0) as turn:
            battle._drive_local(0.1)

        self.assertEqual(1.0, drive.call_args[0][2])
        self.assertEqual(1.0, turn.call_args[0][2])
        self.assertEqual(1.0, turn.call_args.kwargs['drive_intent'])
        self.assertEqual(2.0, battle._local_turn_speed)

    def test_dead_engine_coasts_without_locking_tracks(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        entity.is_engine_dead = True
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=1.0, turn=1.0, handbrake=False,
            send_current=mock.Mock(return_value=True))
        battle._local_descriptor = entity.typeDescriptor
        battle._attach_local_presentation()

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'vehicle_physics.longitudinal_step', return_value=0.0) as drive, \
                mock.patch(
                    'gui.mods.offline_lan_0922.battle_runtime.'
                    'vehicle_physics.traverse_step', return_value=0.0) as turn:
            battle._drive_local(0.1)

        self.assertEqual(0.0, drive.call_args[0][2])
        self.assertFalse(drive.call_args[0][8])
        self.assertEqual(0.0, turn.call_args[0][2])

    def test_live_critical_factors_scale_throttle_and_traverse(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=1.0, turn=1.0, handbrake=False,
            send_current=mock.Mock(return_value=True))
        battle._local_descriptor = entity.typeDescriptor
        battle._attach_local_presentation()

        def factor(unused_entity, stat):
            return {'mobility': 0.5, 'traverse': 0.4}[stat]

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'critical_damage.stat_factor', side_effect=factor), \
                mock.patch(
                    'gui.mods.offline_lan_0922.battle_runtime.'
                    'vehicle_physics.longitudinal_step', return_value=0.0) as drive, \
                mock.patch(
                    'gui.mods.offline_lan_0922.battle_runtime.'
                    'vehicle_physics.traverse_step', return_value=2.0) as turn:
            battle._drive_local(0.1)

        self.assertEqual(0.5, drive.call_args[0][2])
        self.assertEqual(1.0, turn.call_args[0][2])
        self.assertEqual(0.5, turn.call_args.kwargs['drive_intent'])
        self.assertAlmostEqual(0.8, battle._local_turn_speed)

    def test_existing_arcade_camera_tracks_copied_player_matrix(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()
        battle.client = client
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(2, 3, 4), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._binding = mock.Mock()
        battle._sender = _LANInputSender(battle)
        battle._sender.forward = 1.0
        battle._local_position = (2.0, 3.0, 4.0)
        battle._local_yaw = -1.606
        battle._local_descriptor = entity.typeDescriptor
        battle._gun_state = gun_mechanics.GunState(entity.typeDescriptor)
        camera = battle._avatar.inputHandler.\
            _AvatarInputHandler__curCtrl.camera
        stale_matrix = camera.vehicleMProv
        rotator = battle._avatar.gunRotator
        rotator.turretYaw = 0.85
        rotator.gunPitch = -0.12

        def reset_gun_direction():
            rotator.turretYaw = 0.0
            rotator.gunPitch = 0.0

        rotator.reset.side_effect = reset_gun_direction

        battle._attach_local_presentation()
        callbacks_before = len(runtime.bigworld.callbacks)
        battle._bind_local_arcade_camera()
        battle._drive_local(0.1)

        self.assertIsNot(stale_matrix, camera.vehicleMProv)
        self.assertIs(battle._local_matrix, camera.vehicleMProv)
        calculator = battle._avatar.inputHandler.\
            steadyVehicleMatrixCalculator
        output = calculator.\
            _SteadyVehicleMatrixCalculator__outputMProv
        stabilised = calculator.\
            _SteadyVehicleMatrixCalculator__stabilisedMProv
        self.assertIs(battle._local_matrix, output.rotationSrc)
        self.assertIs(battle._local_matrix, output.translationSrc)
        self.assertIs(battle._local_matrix, stabilised.target)
        self.assertEqual(1, camera.direction_resets)
        rotator.reset.assert_called_once_with()
        rotator.start.assert_not_called()
        self.assertEqual([], battle._avatar.gun_tracking_calls)
        self.assertIsNone(rotator.
                          _VehicleGunRotator__maxTurretRotationSpeed)
        self.assertFalse(rotator._VehicleGunRotator__isStarted)
        self.assertFalse(battle._avatar._PlayerAvatar__isOnArena)
        self.assertEqual(callbacks_before, len(runtime.bigworld.callbacks))
        self.assertEqual([], battle._avatar.ammo_updates)
        self.assertEqual([], battle._avatar.reload_updates)
        battle._binding.update_vehicle_aim.assert_called_once_with(
            10, -1.606, -1.606, 0.0)
        self.assertAlmostEqual(-1.606, battle._sender.aim_yaw)
        self.assertAlmostEqual(0.0, battle._sender.gun_pitch)
        self.assertEqual(
            battle._local_position,
            tuple(camera.vehicleMProv.translation))

    @staticmethod
    def _sniper_hull_turn_fixture(turret_yaw_limits):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        descriptor = _Descriptor()
        descriptor.gun.turretYawLimits = turret_yaw_limits
        entity = _Vehicle(10, descriptor, _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._local_descriptor = descriptor
        battle._attach_local_presentation()

        class AimingSystem(object):
            def __init__(self):
                self.world_yaw = 0.8
                self.world_pitch = -0.12
                self.ideal_turret_yaw = 0.8
                self.reset_calls = 0

            def resetIdealDirection(self):
                self.reset_calls += 1
                self.ideal_turret_yaw = (
                    (self.world_yaw - battle._local_matrix.yaw + math.pi) %
                    (2.0 * math.pi) - math.pi)

        aiming = AimingSystem()
        handler = battle._avatar.inputHandler
        handler._AvatarInputHandler__ctrlModeName = 'sniper'
        handler._AvatarInputHandler__curCtrl = types.SimpleNamespace(
            camera=types.SimpleNamespace(aimingSystem=aiming))
        return battle, entity, aiming

    def test_full_turret_sniper_keeps_mouse_world_aim_across_hull_turns(self):
        battle, entity, aiming = self._sniper_hull_turn_fixture(None)

        battle._local_yaw = 0.35
        battle._update_local_presentation(entity)

        self.assertEqual(1, aiming.reset_calls)
        self.assertAlmostEqual(0.8, aiming.world_yaw)
        self.assertAlmostEqual(-0.12, aiming.world_pitch)
        self.assertAlmostEqual(0.45, aiming.ideal_turret_yaw)

        # A mouse step remains world-space input. A pose refresh with no new
        # hull turn must not overwrite or rebase it.
        aiming.world_yaw += 0.1
        battle._update_local_presentation(entity)
        self.assertEqual(1, aiming.reset_calls)
        self.assertAlmostEqual(0.9, aiming.world_yaw)

        battle._local_yaw = 0.55
        battle._update_local_presentation(entity)
        self.assertEqual(2, aiming.reset_calls)
        self.assertAlmostEqual(0.9, aiming.world_yaw)
        self.assertAlmostEqual(0.35, aiming.ideal_turret_yaw)

    def test_limited_traverse_sniper_remains_hull_relative(self):
        battle, entity, aiming = self._sniper_hull_turn_fixture((-0.2, 0.2))

        battle._local_yaw = 0.35
        battle._update_local_presentation(entity)

        self.assertEqual(0, aiming.reset_calls)
        self.assertAlmostEqual(0.8, aiming.ideal_turret_yaw)

    def test_prebattle_draws_the_reticle_but_fences_the_server_marker(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._battle_live = False
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(2, 3, 4), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._gun_state = gun_mechanics.GunState(entity.typeDescriptor)
        battle._config = {
            'prebattleCountdownSeconds': 15.0,
            'battleDurationSeconds': 900.0,
        }
        battle.client = types.SimpleNamespace()
        rotator = battle._avatar.gunRotator
        rotator.showServerMarker = True
        rotator.getCurShotPosition = mock.Mock(return_value=(
            _Vector(0.0, 2.0, 0.0), _Vector(0.0, 0.0, 1.0)))

        def publish_period(period, duration):
            self.assertEqual('prebattle', period)
            self.assertEqual(12.0, duration)
            # Exact #1513 applies the PREBATTLE fence synchronously.
            battle._avatar._PlayerAvatar__isOnArena = False
            rotator._VehicleGunRotator__isStarted = False

        battle._binding = types.SimpleNamespace(arena_period=publish_period)

        self.assertTrue(battle.on_battle_live({
            'countdown_seconds': 12.0,
            'battle_duration_seconds': 900.0,
        }))
        # The reticle is drawn during our countdown, but the server-marker
        # echo still waits for the BATTLE transition.
        self.assertFalse(battle._sync_local_server_marker())
        rotator.start.assert_called_once_with()
        rotator.getCurShotPosition.assert_not_called()
        self.assertEqual([], battle._avatar.inputHandler.started_periods)
        self.assertEqual([], battle._avatar.gun_marker_updates)
        self.assertEqual(
            [(True, 1)], battle._avatar.inputHandler.gun_marker_flags)
        self.assertEqual([True], battle._avatar.inputHandler.client_markers)
        self.assertTrue(battle._avatar._PlayerAvatar__isOnArena)
        self.assertFalse(battle._battle_live)
        # isOnArena is also PlayerAvatar.shoot's first gate, so the countdown
        # raises retail's second gate to keep laying and firing frozen.
        self.assertTrue(battle._avatar.isGunLocked)
        self.assertEqual([True], battle._avatar.gun_locks)

    def test_battle_transition_starts_one_native_gun_timer_from_zero(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._battle_live = False
        battle._avatar = runtime.bigworld.avatar
        battle._config = {
            'prebattleCountdownSeconds': 15.0,
            'battleDurationSeconds': 900.0,
        }
        battle.client = types.SimpleNamespace()
        rotator = battle._avatar.gunRotator
        rotator.turretYaw = 0.0
        rotator.gunPitch = 0.0
        rotator._VehicleGunRotator__maxTurretRotationSpeed = 1.0
        rotator._VehicleGunRotator__isStarted = False
        periods = []

        def publish_period(period, duration):
            periods.append((period, duration))
            if period == 'prebattle':
                battle._avatar._PlayerAvatar__isOnArena = False
                rotator._VehicleGunRotator__isStarted = False
            else:
                self.assertEqual('battle', period)
                battle._avatar._PlayerAvatar__isOnArena = True
                battle._avatar.inputHandler.\
                    _AvatarInputHandler__onArenaStarted(
                        runtime.constants.ARENA_PERIOD.BATTLE)
                rotator.start()

        battle._binding = types.SimpleNamespace(arena_period=publish_period)

        self.assertTrue(battle.on_battle_live({
            'countdown_seconds': 12.0,
            'battle_duration_seconds': 900.0,
        }))
        # The countdown already draws the reticle, so the rotator is running
        # before the BATTLE transition; stock start() is idempotent.
        rotator.start.assert_called_once_with()
        self.assertEqual([('prebattle', 12.0)], periods)
        self.assertTrue(battle._begin_battle())
        self.assertFalse(battle._begin_battle())
        self.assertEqual(2, rotator.start.call_count)
        self.assertEqual(
            ['start', 'start'], battle._avatar.gun_tracking_calls)
        # The battle transition releases the countdown's gun lock.
        self.assertFalse(battle._avatar.isGunLocked)
        self.assertEqual([True, False], battle._avatar.gun_locks)
        self.assertEqual(
            [runtime.constants.ARENA_PERIOD.BATTLE],
            battle._avatar.inputHandler.started_periods)
        self.assertEqual(0.0, rotator.turretYaw)
        self.assertEqual(0.0, rotator.gunPitch)
        self.assertTrue(rotator._VehicleGunRotator__isStarted)
        self.assertTrue(battle._avatar._PlayerAvatar__isOnArena)
        self.assertTrue(battle._battle_live)

    def test_prebattle_server_marker_waits_for_battle(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._battle_live = False
        battle._avatar = runtime.bigworld.avatar
        battle._server = types.SimpleNamespace(vehicle_id=10)
        rotator = battle._avatar.gunRotator
        rotator.showServerMarker = True
        rotator.getCurShotPosition = mock.Mock()

        self.assertFalse(battle._sync_local_server_marker())
        rotator.getCurShotPosition.assert_not_called()
        self.assertEqual([], battle._avatar.gun_marker_updates)

        battle._battle_live = True
        rotator.getCurShotPosition.return_value = (
            _Vector(0.0, 2.0, 0.0), _Vector(0.0, 0.0, 1.0))
        self.assertTrue(battle._sync_local_server_marker())
        rotator.getCurShotPosition.assert_called_once_with()
        self.assertEqual(1, len(battle._avatar.gun_marker_updates))

    def test_sniper_transition_rejects_stale_steady_sources(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(2, 3, 4),
                          (0, 0, 0), {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._local_position = (2.0, 3.0, 4.0)
        battle._local_descriptor = entity.typeDescriptor
        battle._attach_local_presentation()
        handler = battle._avatar.inputHandler
        calculator = handler.steadyVehicleMatrixCalculator
        output = calculator.\
            _SteadyVehicleMatrixCalculator__outputMProv
        stabilised = calculator.\
            _SteadyVehicleMatrixCalculator__stabilisedMProv
        output.rotationSrc = object()
        output.translationSrc = object()
        stabilised.target = object()
        handler._AvatarInputHandler__ctrlModeName = 'sniper'

        with self.assertRaisesRegex(
                RuntimeError, 'captured a stale vehicle pose'):
            battle._on_control_mode_changed(handler, 'sniper')

        output.rotationSrc = battle._local_matrix
        output.translationSrc = battle._local_matrix
        stabilised.target = battle._local_matrix
        self.assertTrue(battle._on_control_mode_changed(handler, 'sniper'))

    def test_postmortem_delay_uses_live_player_vehicle_matrix(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.playerVehicleID = 10
        battle._local_matrix = _Matrix()
        attached = types.SimpleNamespace(target=battle._local_matrix)
        battle._avatar.consistentMatrices = types.SimpleNamespace(
            attachedVehicleMatrix=attached)
        vehicle = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                           {'health': 0})
        vehicle.matrix = battle._local_matrix
        runtime.bigworld.entities[10] = vehicle
        handler = battle._avatar.inputHandler
        camera = types.SimpleNamespace(vehicleMProv=battle._local_matrix)
        control = types.SimpleNamespace(
            _PostMortemControlMode__cam=camera,
            curPostmortemDelay=object())
        handler._AvatarInputHandler__curCtrl = control
        handler._AvatarInputHandler__ctrlModeName = 'postmortem'

        self.assertTrue(battle._on_control_mode_changed(
            handler, 'postmortem'))

        camera.vehicleMProv = attached
        with self.assertRaisesRegex(
                RuntimeError,
                'postmortem camera captured a stale vehicle pose'):
            battle._on_control_mode_changed(handler, 'postmortem')

        camera.vehicleMProv = battle._local_matrix
        vehicle.matrix = object()
        with self.assertRaisesRegex(
                RuntimeError,
                'postmortem vehicle captured a stale vehicle pose'):
            battle._on_control_mode_changed(handler, 'postmortem')

        vehicle.matrix = battle._local_matrix
        control.curPostmortemDelay = None
        camera.vehicleMProv = attached
        self.assertTrue(battle._on_control_mode_changed(
            handler, 'postmortem'))

        attached.target = object()
        with self.assertRaisesRegex(
                RuntimeError,
                'postmortem attached provider captured a stale vehicle '
                'pose'):
            battle._on_control_mode_changed(handler, 'postmortem')

        del attached.target
        with self.assertRaisesRegex(
                RuntimeError,
                'attached vehicle matrix target is unavailable'):
            battle._on_control_mode_changed(handler, 'postmortem')

    def test_postmortem_delay_uses_steady_matrix_after_vehicle_removed(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.playerVehicleID = 10
        battle._local_matrix = _Matrix()
        attached = types.SimpleNamespace(target=battle._local_matrix)
        battle._avatar.consistentMatrices = types.SimpleNamespace(
            attachedVehicleMatrix=attached)
        handler = battle._avatar.inputHandler
        calculator = handler.steadyVehicleMatrixCalculator
        output = calculator.\
            _SteadyVehicleMatrixCalculator__outputMProv
        output.rotationSrc = battle._local_matrix
        output.translationSrc = battle._local_matrix
        calculator.outputMProv = output
        camera = types.SimpleNamespace(vehicleMProv=output)
        handler._AvatarInputHandler__curCtrl = types.SimpleNamespace(
            _PostMortemControlMode__cam=camera,
            curPostmortemDelay=object())
        handler._AvatarInputHandler__ctrlModeName = 'postmortem'

        self.assertTrue(battle._on_control_mode_changed(
            handler, 'postmortem'))

        output.translationSrc = object()
        with self.assertRaisesRegex(
                RuntimeError,
                'postmortem delay captured a stale vehicle pose'):
            battle._on_control_mode_changed(handler, 'postmortem')

    @staticmethod
    def _postmortem_switch_fixture():
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.playerVehicleID = 10
        battle._local_matrix = _Matrix()
        battle._local_position = (0.0, 0.0, 0.0)
        battle._server = types.SimpleNamespace(vehicle_id=10)
        local = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                         {'health': 0})
        local.matrix = battle._local_matrix
        runtime.bigworld.entities[10] = local
        battle._records = {'player:1': {
            'engine_id': 10,
            'state': {'team': 1, 'health': 0, 'alive': False},
            'kind': 'player', 'network_id': 1, 'local': True,
            'ready': True}}
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        battle._remote_factory = factory
        handler = battle._avatar.inputHandler
        camera = types.SimpleNamespace(vehicleMProv=types.SimpleNamespace())
        handler._AvatarInputHandler__curCtrl = types.SimpleNamespace(
            _PostMortemControlMode__cam=camera, curPostmortemDelay=None)
        handler._AvatarInputHandler__ctrlModeName = 'postmortem'
        battle._avatar.consistentMatrices._ConsistentMatrices__setTarget(
            battle._local_matrix, False)
        camera.vehicleMProv = \
            battle._avatar.consistentMatrices.attachedVehicleMatrix
        battle._spectated_engine_id = 10
        return runtime, battle, factory, camera

    def test_postmortem_switch_attaches_live_friendly_bot_and_calls_stock(self):
        runtime, battle, factory, camera = \
            self._postmortem_switch_fixture()
        ally_id = factory.create(
            _Descriptor(),
            {'publicInfo': {'team': 1, 'name': 'Ally'},
             'health': 500, 'isCrewActive': True,
             'gunAnglesPacked': 0},
            _Vector(20.0, 0.0, 10.0), (0.0, 0.0, 0.0))
        ally = factory.get(ally_id)
        battle._records['bot:2'] = {
            'engine_id': ally_id,
            'state': {'team': 1, 'health': 500, 'alive': True},
            'kind': 'bot', 'network_id': 2, 'local': False,
            'ready': True, 'presentation': True}

        self.assertIsNone(runtime.bigworld.entity(ally_id))
        self.assertTrue(battle._switch_postmortem_viewpoint(False, ally_id))

        self.assertIs(ally.matrix,
                      battle._avatar.consistentMatrices.
                      attachedVehicleMatrix.target)
        self.assertIs(battle._avatar.consistentMatrices.
                      attachedVehicleMatrix, camera.vehicleMProv)
        self.assertIs(ally, runtime.bigworld.entity(ally_id))
        self.assertIn(ally_id, runtime.bigworld.entities.keys())
        self.assertEqual(ally_id, battle._spectated_engine_id)
        self.assertEqual(
            ally_id, runtime.compatibility.postmortem_vehicle_id)
        self.assertEqual(ally_id,
                         battle._avatar.viewpoint_switches[-1][0])

    def test_postmortem_switch_does_not_test_pyentities_membership(self):
        runtime, battle, factory, unused_camera = \
            self._postmortem_switch_fixture()
        ally_id = factory.create(
            _Descriptor(),
            {'publicInfo': {'team': 1, 'name': 'Ally'},
             'health': 500, 'isCrewActive': True,
             'gunAnglesPacked': 0},
            _Vector(20.0, 0.0, 10.0), (0.0, 0.0, 0.0))
        battle._records['bot:2'] = {
            'engine_id': ally_id,
            'state': {'team': 1, 'health': 500, 'alive': True},
            'kind': 'bot', 'network_id': 2, 'local': False,
            'ready': True, 'presentation': True}

        entities = runtime.bigworld.entities

        class _PyEntities(object):

            def get(self, key, default=None):
                return entities.get(key, default)

            def keys(self):
                return entities.keys()

            def __contains__(self, unused_key):
                raise TypeError('PyEntities does not support membership')

        runtime.bigworld.entities = _PyEntities()

        self.assertTrue(
            battle._switch_postmortem_viewpoint(False, ally_id))
        self.assertIs(factory.get(ally_id),
                      runtime.bigworld.entity(ally_id))

    def test_postmortem_switch_survives_exact_1513_steady_matrix_relink(self):
        runtime, battle, factory, unused_camera = \
            self._postmortem_switch_fixture()
        descriptor = _Descriptor()
        descriptor.isPitchHullAimingAvailable = False
        ally_id = factory.create(
            descriptor,
            {'publicInfo': {'team': 1, 'name': 'Ally'},
             'health': 500, 'isCrewActive': True,
             'gunAnglesPacked': 0},
            _Vector(20.0, 0.0, 10.0), (0.0, 0.0, 0.0))
        ally = factory.get(ally_id)
        battle._records['bot:2'] = {
            'engine_id': ally_id,
            'state': {'team': 1, 'health': 500, 'alive': True},
            'kind': 'bot', 'network_id': 2, 'local': False,
            'ready': True, 'presentation': True}

        matrices = battle._avatar.consistentMatrices
        original_set_target = matrices._ConsistentMatrices__setTarget
        calculator = battle._avatar.inputHandler.\
            steadyVehicleMatrixCalculator

        def set_target_and_relink(matrix, as_static):
            original_set_target(matrix, as_static)
            selected = runtime.bigworld.entity(
                runtime.compatibility.postmortem_vehicle_id)
            if selected is None:
                return
            output = calculator.\
                _SteadyVehicleMatrixCalculator__outputMProv
            stabilised = calculator.\
                _SteadyVehicleMatrixCalculator__stabilisedMProv
            # Exact #1513 SteadyVehicleMatrixCalculator.relinkSources.
            if selected.typeDescriptor.isPitchHullAimingAvailable:
                output.rotationSrc = \
                    selected.filter.groundPlacingMatrixFiltered
                output.translationSrc = selected.filter.stabilisedMatrix
            else:
                output.rotationSrc = selected.filter.stabilisedMatrix
                output.translationSrc = output.rotationSrc
            stabilised.target = selected.filter.stabilisedMatrix

        matrices._ConsistentMatrices__setTarget = set_target_and_relink

        self.assertTrue(
            battle._switch_postmortem_viewpoint(False, ally_id))
        output = calculator._SteadyVehicleMatrixCalculator__outputMProv
        stabilised = calculator.\
            _SteadyVehicleMatrixCalculator__stabilisedMProv
        self.assertIs(ally.matrix, output.rotationSrc)
        self.assertIs(ally.matrix, output.translationSrc)
        self.assertIs(ally.matrix, stabilised.target)

    def test_postmortem_switch_cycles_live_friendly_bot_and_human(self):
        runtime, battle, factory, unused_camera = \
            self._postmortem_switch_fixture()
        targets = []
        for key, kind, network_id, position in (
                ('bot:2', 'bot', 2, (20.0, 0.0, 10.0)),
                ('player:3', 'player', 3, (30.0, 0.0, 10.0))):
            engine_id = factory.create(
                _Descriptor(),
                {'publicInfo': {'team': 1, 'name': key},
                 'health': 500, 'isCrewActive': True,
                 'gunAnglesPacked': 0},
                _Vector(position), (0.0, 0.0, 0.0))
            battle._records[key] = {
                'engine_id': engine_id,
                'state': {'team': 1, 'health': 500, 'alive': True},
                'kind': kind, 'network_id': network_id, 'local': False,
                'ready': True, 'presentation': True}
            targets.append(engine_id)

        for engine_id in targets:
            self.assertTrue(
                battle._switch_postmortem_viewpoint(False, engine_id))

        bot = factory.get(targets[0])
        human = factory.get(targets[1])
        self.assertFalse(bot._postmortem_visible)
        self.assertTrue(human._postmortem_visible)
        self.assertEqual(targets, [
            vehicle_id for vehicle_id, unused_position in
            battle._avatar.viewpoint_switches])
        self.assertIs(human, runtime.bigworld.entity(targets[1]))

    def test_postmortem_switch_rejects_enemy_dead_and_active_delay(self):
        unused_runtime, battle, factory, unused_camera = \
            self._postmortem_switch_fixture()
        enemy_id = factory.create(
            _Descriptor(),
            {'publicInfo': {'team': 2, 'name': 'Enemy'},
             'health': 500, 'isCrewActive': True,
             'gunAnglesPacked': 0},
            _Vector(), (0.0, 0.0, 0.0))
        enemy = factory.get(enemy_id)
        battle._records['bot:2'] = {
            'engine_id': enemy_id,
            'state': {'team': 2, 'health': 500, 'alive': True},
            'kind': 'bot', 'network_id': 2, 'local': False,
            'ready': True, 'presentation': True}

        self.assertFalse(battle._switch_postmortem_viewpoint(False, enemy_id))
        battle._records['bot:2']['state']['team'] = 1
        battle._records['bot:2']['state']['health'] = 0
        battle._records['bot:2']['state']['alive'] = False
        enemy.onHealthChanged(0)
        self.assertFalse(battle._switch_postmortem_viewpoint(False, enemy_id))
        battle._records['bot:2']['state'].update(
            health=500, alive=True)
        enemy.onHealthChanged(500)
        battle._avatar.inputHandler._AvatarInputHandler__curCtrl.\
            curPostmortemDelay = object()
        self.assertFalse(battle._switch_postmortem_viewpoint(False, enemy_id))
        self.assertEqual([], battle._avatar.viewpoint_switches)

    def test_observed_ally_death_falls_back_to_nearest_live_ally(self):
        unused_runtime, battle, factory, unused_camera = \
            self._postmortem_switch_fixture()
        records = []
        for network_id, position in ((2, (30.0, 0.0, 0.0)),
                                     (3, (10.0, 0.0, 0.0))):
            engine_id = factory.create(
                _Descriptor(),
                {'publicInfo': {'team': 1, 'name': 'Ally'},
                 'health': 500, 'isCrewActive': True,
                 'gunAnglesPacked': 0},
                _Vector(position), (0.0, 0.0, 0.0))
            record = {
                'engine_id': engine_id,
                'state': {'team': 1, 'health': 500, 'alive': True},
                'kind': 'bot', 'network_id': network_id, 'local': False,
                'ready': True, 'presentation': True}
            battle._records['bot:%s' % network_id] = record
            records.append(record)
        observed, nearest = records
        battle._spectated_engine_id = observed['engine_id']
        factory.get(observed['engine_id'])._postmortem_visible = True

        self.assertTrue(battle._fallback_postmortem_viewpoint(
            observed['engine_id']))

        self.assertEqual(nearest['engine_id'], battle._spectated_engine_id)
        self.assertFalse(factory.get(
            observed['engine_id'])._postmortem_visible)
        self.assertTrue(factory.get(
            nearest['engine_id'])._postmortem_visible)

    def test_local_rpm_and_gear_use_avatar_aux_physics_properties(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        battle._local_descriptor = _Descriptor()

        self.assertTrue(battle._publish_rpm(10.0, force=True))
        battle._binding.avatar_aux_physics.assert_called_once_with(
            0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0)

        battle._local_speed = 7.0
        self.assertTrue(battle._publish_rpm(10.1))
        args = battle._binding.avatar_aux_physics.call_args.args
        self.assertEqual((0.0, 0.0, 0.0, 0.0, 0.0), args[:5])
        self.assertEqual(1.0, args[5])
        self.assertEqual(1, args[6])
        self.assertFalse(battle._publish_rpm(10.15))

        # RPM and gear can remain stable while copied pose/track inputs move.
        # The packed auxiliary property must still follow that native state.
        battle._local_yaw = 0.25
        self.assertTrue(battle._publish_rpm(10.2))
        self.assertEqual(
            0.25, battle._binding.avatar_aux_physics.call_args.args[0])
        self.assertFalse(battle._publish_rpm(10.3))

    def test_native_gun_stabilised_provider_tracks_copied_player_matrix(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(2, 3, 4),
                          (0, 0, 0), {'health': 500})
        native_matrix = entity.matrix
        entity.filter.stabilisedMatrix = native_matrix
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._local_position = (2.0, 3.0, 4.0)

        battle._attach_local_presentation()
        provider = battle._avatar._PlayerAvatar__ownVehicleStabMProv

        self.assertIs(battle._local_matrix, provider.target)
        battle._local_yaw = 1.25
        battle._local_position = (5.0, 3.0, 9.0)
        battle._update_local_presentation(entity)
        self.assertAlmostEqual(1.25, provider.target.yaw)
        self.assertEqual((5.0, 3.0, 9.0),
                         tuple(provider.target.translation))

        battle._detach_local_presentation()

        self.assertIs(native_matrix, provider.target)

    def test_remote_snapshot_applies_confirmed_suspension_rotation(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._binding = mock.Mock()
        battle._apply_record_pose(
            {'engine_id': 17, 'local': False, 'state': {}},
            {'x': 2.0, 'y': 3.0, 'z': 4.0, 'yaw': 0.6,
             'pitch': -0.14, 'roll': 0.08})

        args = battle._binding.set_vehicle_pose.call_args.args
        self.assertEqual(17, args[0])
        self.assertEqual(_engine_rotation(0.6, -0.14, 0.08), args[2])

    def test_hidden_worker_remote_pose_skips_tracks_but_keeps_pose_and_aim(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._worker_mode = True
        battle._binding = mock.Mock()
        battle._remote_factory = mock.Mock()
        record = {
            'engine_id': 17, 'kind': 'player', 'network_id': 4,
            'local': False,
            'state': {'id': 4, 'speed': 5.0,
                      'health': 500, 'alive': True}}

        self.assertTrue(battle._apply_record_pose(record, {
            'x': 2.0, 'y': 3.0, 'z': 4.0, 'yaw': 0.6,
            'pitch': -0.14, 'roll': 0.08,
            'aim_yaw': 0.7, 'gun_pitch': -0.05}))

        battle._binding.set_vehicle_pose.assert_called_once()
        battle._binding.update_vehicle_aim.assert_called_once_with(
            17, 0.6, 0.7, -0.05)
        battle._remote_factory.get.assert_not_called()

    def test_guest_snapshot_drives_tracks_from_interpolated_hull_turn(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._binding = mock.Mock()
        battle._clock = mock.Mock(side_effect=(10.0, 10.1))
        battle._update_bot_tracks = mock.Mock(return_value=True)
        record = {
            'engine_id': 17, 'kind': 'bot', 'network_id': 3,
            'local': False,
            'state': {'id': 3, 'speed': 0.0,
                      'health': 500, 'alive': True}}

        battle._apply_record_pose(record, {
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0})
        battle._apply_record_pose(record, {
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.1})

        first = battle._update_bot_tracks.call_args_list[0].args
        second = battle._update_bot_tracks.call_args_list[1].args
        self.assertEqual(0.0, first[3])
        self.assertAlmostEqual(1.0, second[3])

        battle._clock = mock.Mock(side_effect=(20.0, 20.1))
        battle._update_bot_tracks.reset_mock()
        record = {
            'engine_id': 18, 'kind': 'player', 'network_id': 4,
            'local': False,
            'state': {'id': 4, 'speed': 5.0,
                      'health': 500, 'alive': True}}
        battle._apply_record_pose(record, {
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.2})
        battle._apply_record_pose(record, {
            'x': 0.5, 'y': 0.0, 'z': 0.0, 'yaw': 0.1})
        self.assertEqual(2, battle._update_bot_tracks.call_count)
        self.assertAlmostEqual(
            -1.0, battle._update_bot_tracks.call_args.args[3])

    def test_bot_authority_handoff_switches_only_bot_interpolation(self):
        battle = BattleRuntime(_runtime())
        battle.client = types.SimpleNamespace(player_id=1)
        battle._binding = mock.Mock()
        battle._remote_factory = types.SimpleNamespace(
            set_entity_interpolate_motion=mock.Mock(return_value=True))
        battle._records = {
            'bot:3': {
                'engine_id': 17, 'kind': 'bot', 'native_remote': True,
                'tombstone': False, 'ready': True,
                'visual_started': True,
                'track_pose_sample': (4.0, 0.2)},
            'player:2': {
                'engine_id': 18, 'kind': 'player', 'native_remote': True,
                'tombstone': False},
        }

        self.assertTrue(battle._set_bot_presentation_interpolation(-1))
        battle._remote_factory.set_entity_interpolate_motion.\
            assert_called_once_with(17, False)
        battle._binding.refresh_vehicle_minimap.assert_called_once_with(17)
        self.assertNotIn('track_pose_sample', battle._records['bot:3'])

        battle._remote_factory.set_entity_interpolate_motion.reset_mock()
        battle._binding.refresh_vehicle_minimap.reset_mock()
        self.assertTrue(battle._set_bot_presentation_interpolation(1))
        battle._remote_factory.set_entity_interpolate_motion.\
            assert_called_once_with(17, True)
        battle._binding.refresh_vehicle_minimap.assert_called_once_with(17)

    def test_countdown_hides_native_enemies_and_rebinds_friendly_minimap(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = types.SimpleNamespace(team=1)
        battle._binding = mock.Mock()
        friendly = _Vehicle(
            10, _Descriptor(), _Vector(), (0.0, 0.0, 0.0),
            {'health': 500, 'publicInfo': {'team': 1}})
        enemy = _Vehicle(
            11, _Descriptor(), _Vector(), (0.0, 0.0, 0.0),
            {'health': 500, 'publicInfo': {'team': 2}})
        enemy._spot_visible = True
        enemy._offlineNativeDrawVisible = True
        enemy.targetCaps = [1]
        vehicles = {10: friendly, 11: enemy}
        battle._remote_factory = types.SimpleNamespace(
            get=lambda entity_id: vehicles.get(entity_id))
        battle._records = {
            'bot:1': {
                'engine_id': 10, 'local': False, 'native_remote': True,
                'ready': True, 'tombstone': False,
                'state': {'team': 1}},
            'bot:2': {
                'engine_id': 11, 'local': False, 'native_remote': True,
                'ready': True, 'tombstone': False,
                # Deliberately stale: stock may have re-added the marker
                # after runtime state already recorded it as stopped.
                'visual_started': False, 'spot_visible': True,
                'state': {'team': 2}},
        }

        self.assertTrue(battle._reset_prebattle_native_visuals())

        battle._binding.refresh_vehicle_minimap.assert_called_once_with(10)
        battle._binding.stop_vehicle_visual.assert_called_once_with(11, False)
        self.assertFalse(enemy.model.visible)
        self.assertEqual([], enemy.targetCaps)
        self.assertFalse(enemy._spot_visible)
        self.assertFalse(enemy._offlineNativeDrawVisible)
        self.assertEqual([False], enemy.visibility_changes)
        self.assertEqual([False], enemy.shows)
        self.assertFalse(battle._records['bot:2']['visual_started'])
        self.assertTrue(battle._records['bot:1']['native_minimap_rebound'])

        battle._reset_prebattle_native_visuals()
        battle._binding.refresh_vehicle_minimap.assert_called_once_with(10)

    def test_native_reveal_restores_draw_pass_after_initial_enemy_hide(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        enemy = _Vehicle(
            1000, _Descriptor(), _Vector(100.0, 0.0, 0.0),
            (0.0, 0.0, 0.0), {'health': 500})
        battle._remote_factory = types.SimpleNamespace(
            get=lambda entity_id: enemy if entity_id == 1000 else None)
        record = {
            'engine_id': 1000, 'kind': 'bot', 'network_id': 17,
            'ready': True, 'local': False, 'presentation': True,
            'native_remote': True, 'visual_started': False,
            'spot_visible': False, 'spot_marker_visible': False,
            'state': {'team': 2, 'health': 500, 'alive': True}}

        # Exact #1513's initial compatibility gate calls Vehicle.show(False),
        # selecting its shadow-only draw pass before runtime spotting starts.
        enemy.show(False)
        enemy.appearance.changeVisibility(False)
        self.assertFalse(enemy.draw_pass_visible)
        self.assertFalse(enemy.model.visible)

        self.assertTrue(battle._set_record_spot_visibility(
            record, True, True))

        self.assertTrue(enemy.draw_pass_visible)
        self.assertTrue(enemy.model.visible)
        self.assertEqual([False, True], enemy.shows)
        self.assertEqual([False, True], enemy.visibility_changes)

    def test_native_reverse_sample_preserves_yaw_component_order(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()
        battle.client = client
        battle._avatar = runtime.bigworld.avatar
        yaw = 2.947
        entity = _Vehicle(
            10, _Descriptor(),
            _Vector(-math.sin(yaw) * 2.0, 0.0,
                    -math.cos(yaw) * 2.0),
            _engine_rotation(yaw), {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=-1.0, turn=0.0, handbrake=False,
            send_current=lambda: client.send_input('current'))
        battle._local_position = (0.0, 0.0, 0.0)
        battle._local_yaw = yaw
        battle._local_speed = -3.0
        battle._local_descriptor = entity.typeDescriptor
        battle._attach_local_presentation()

        battle._drive_local(0.1)

        displacement_along_forward = (
            math.sin(yaw) * battle._local_position[0] +
            math.cos(yaw) * battle._local_position[2])
        self.assertLess(battle._local_speed, 0.0)
        self.assertLess(displacement_along_forward, 0.0)
        direction = runtime.bigworld.avatar.positions[-1][1]
        self.assertAlmostEqual(0.0, direction.x)
        self.assertAlmostEqual(0.0, direction.y)
        self.assertAlmostEqual(yaw, direction.z)

    def test_local_pose_tracks_successive_copied_physics_steps(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()
        battle.client = client
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(0, 0, 0), (0, 0, 0),
                          {'health': 500})

        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=1.0, turn=0.0, handbrake=False,
            send_current=lambda: client.send_input('current'))
        battle._local_descriptor = entity.typeDescriptor
        battle._attach_local_presentation()

        battle._drive_local(0.02)
        first_z = battle._local_position[2]
        battle._drive_local(0.02)

        self.assertGreater(battle._local_position[2], first_z)
        self.assertEqual([], entity.teleports)

    def test_copied_integrator_owns_player_collision_and_vertical_motion(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()
        battle.client = client
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(4, -5, 8), (0, 0, 0),
                          {'health': 500})
        entity.speed = 4.0
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=1.0, turn=0.0, handbrake=False,
            send_current=lambda: client.send_input('current'))
        battle._local_position = (4.0, -5.0, 8.0)
        battle._local_descriptor = entity.typeDescriptor
        battle._attach_local_presentation()
        battle._motion_is_clear = mock.Mock(return_value=True)
        battle._ground_y = mock.Mock(return_value=0.0)

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'vehicle_physics.longitudinal_step', return_value=4.0) as step:
            battle._drive_local(0.02)

        step.assert_called_once()
        battle._motion_is_clear.assert_called()
        battle._ground_y.assert_called()
        self.assertGreater(battle._local_position[2], 8.0)
        self.assertEqual(4.0, battle._local_speed)

    def test_grounded_player_rejects_raised_support_as_hard_collision(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()
        battle.client = client
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(0, 0, 0), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=1.0, turn=0.0, handbrake=False,
            send_current=lambda: client.send_input('current'))
        battle._local_descriptor = entity.typeDescriptor
        battle._attach_local_presentation()
        battle._local_fall_armed = True
        battle._motion_is_clear = mock.Mock(return_value=True)
        battle._terrain_support = mock.Mock(return_value=(1.4, 1.4))
        battle._ground_pitch = mock.Mock(return_value=0.0)
        battle._resolve_local_tank_contacts = mock.Mock(
            side_effect=lambda unused_entity, position, unused_yaw,
            unused_dt: position)

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'vehicle_physics.longitudinal_step', return_value=4.0):
            battle._drive_local(0.02)

        self.assertEqual((0.0, 0.0, 0.0), battle._local_position)
        self.assertAlmostEqual(4.0 * 0.35 ** 1.2, battle._local_speed)
        self.assertEqual(4, battle._local_grind)
        self.assertFalse(battle._local_airborne)
        battle._motion_is_clear.assert_called_once()

    def test_layered_support_skips_trench_edge_and_keeps_the_floor(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar

        def collision(unused_space, start, unused_end, unused_mask,
                      *unused_filter):
            if start.y > 1.35:
                return (_Vector(start.x, 1.4, start.z),
                        _Vector(0.0, 1.0, 0.0))
            return (_Vector(start.x, 0.0, start.z),
                    _Vector(0.0, 1.0, 0.0))

        runtime.bigworld.wg_collideSegment = collision

        self.assertEqual(
            (1.4, 1.4),
            battle._terrain_support(
                (0.0, 0.0, 0.0), 0.0, _Descriptor()))
        self.assertEqual(
            (0.0, 0.0),
            battle._terrain_support(
                (0.0, 0.0, 0.0), 0.0, _Descriptor(), maximum_y=0.62))

    def test_trench_upper_support_does_not_rollback_clear_horizontal_drive(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()
        battle.client = client
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=1.0, turn=0.0, handbrake=False,
            send_current=lambda: client.send_input('current'))
        battle._local_descriptor = entity.typeDescriptor
        battle._attach_local_presentation()
        battle._local_fall_armed = True
        battle._motion_is_clear = mock.Mock(return_value=True)
        battle._terrain_support = mock.Mock(
            side_effect=((1.4, 1.4), (0.0, 0.0)))
        battle._ground_pitch = mock.Mock(return_value=0.0)
        battle._resolve_local_tank_contacts = mock.Mock(
            side_effect=lambda unused_entity, position, unused_yaw,
            unused_dt: position)

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'vehicle_physics.longitudinal_step', return_value=4.0):
            battle._drive_local(0.02)

        self.assertGreater(battle._local_position[2], 0.0)
        self.assertEqual(0.0, battle._local_position[1])
        self.assertFalse(battle._local_support_rise_blocked)
        self.assertAlmostEqual(
            0.62,
            battle._terrain_support.call_args_list[1].kwargs['maximum_y'])

    def test_first_streamed_ground_snaps_spawn_without_fall_damage(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(0, 100, 0),
                          (0, 0, 0), {'health': 500})

        position = battle._update_vertical_motion(
            entity, (0.0, 100.0, 0.0), 0.0, 0.04)

        self.assertEqual((0.0, 0.0, 0.0), position)
        self.assertFalse(battle._local_support_rise_blocked)
        self.assertTrue(battle._local_fall_armed)
        self.assertFalse(battle._local_airborne)
        self.assertEqual(500, entity.health)

    def test_first_ground_support_rise_is_still_a_spawn_snap(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        battle._terrain_support = mock.Mock(return_value=(1.4, 1.4))

        position = battle._update_vertical_motion(
            entity, (0.0, 0.0, 0.0), 0.0, 0.04)

        self.assertEqual((0.0, 1.4, 0.0), position)
        self.assertTrue(battle._local_fall_armed)
        self.assertFalse(battle._local_support_rise_blocked)

    def test_grounded_hull_uses_real_gap_for_remote_edge_support(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._local_fall_armed = True
        battle._local_speed = 15.0
        battle._terrain_support = mock.Mock(return_value=(-20.0, None))
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})

        position = battle._update_vertical_motion(
            entity, (0.0, 0.0, 0.0), 0.0, 0.04)

        self.assertTrue(battle._local_airborne)
        self.assertLess(battle._local_vertical_speed, 0.0)
        self.assertGreater(position[1], -1.0)

    def test_grounded_hull_keeps_near_support_across_narrow_gap(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._local_fall_armed = True
        battle._terrain_support = mock.Mock(return_value=(0.0, None))
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})

        position = battle._update_vertical_motion(
            entity, (0.0, 0.0, 0.0), 0.0, 0.04)

        self.assertEqual((0.0, 0.0, 0.0), position)
        self.assertFalse(battle._local_airborne)

    def test_high_speed_cliff_pitch_does_not_snap_to_lower_ground(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._local_fall_armed = True
        battle._local_speed = 20.0
        battle._local_last_pitch = math.atan(0.5)
        battle._terrain_support = mock.Mock(return_value=(-2.0, -2.0))
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})

        position = battle._update_vertical_motion(
            entity, (0.0, 0.0, 0.0), 0.0, 0.1)

        self.assertTrue(battle._local_airborne)
        self.assertLess(battle._local_vertical_speed, 0.0)
        self.assertGreater(position[1], -2.0)

    def test_armed_ledge_fall_only_queues_an_impact_observation(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        entity = _Vehicle(10, _Descriptor(), _Vector(0, 20, 0),
                          (0, 0, 0), {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._records = {
            'player:1': {
                'engine_id': 10, 'state': {'health': 500, 'alive': True},
                'kind': 'player', 'network_id': 1, 'local': True}}
        battle._local_fall_armed = True
        position = (0.0, 20.0, 0.0)
        entity.onHealthChanged = mock.Mock(wraps=entity.onHealthChanged)

        for unused in range(30):
            position = battle._update_vertical_motion(
                entity, position, 0.0, 0.1)
            self.assertFalse(battle._local_support_rise_blocked)
            if not battle._local_airborne:
                break

        self.assertEqual(0.0, position[1])
        self.assertFalse(battle._local_airborne)
        self.assertEqual(500, entity.health)
        self.assertIsNone(battle._local_damage_report)
        self.assertEqual(500,
                         battle._records['player:1']['state']['health'])
        self.assertEqual(1, len(battle._pending_landing_impacts))
        self.assertGreater(battle._pending_landing_impacts[0], 10.0)
        landed_health = entity.health
        for unused in range(10):
            position = battle._update_vertical_motion(
                entity, position, 0.0, 0.1)
        self.assertEqual(landed_health, entity.health)
        self.assertEqual(0, entity.onHealthChanged.call_count)

    def test_landing_publish_sends_pose_first_and_retries_without_loss(self):
        battle = BattleRuntime(_runtime())
        calls = []
        landing_results = [False, 1]
        battle._sender = types.SimpleNamespace(
            send_current=lambda: calls.append('pose') or True)
        battle.client = types.SimpleNamespace(
            send_landing_observation=lambda speed: (
                calls.append(('landing', speed)) or
                landing_results.pop(0)))
        battle._pending_landing_impacts = [18.5, 22.0]

        self.assertFalse(battle._flush_landing_observation())
        self.assertEqual([18.5, 22.0], battle._pending_landing_impacts)
        self.assertTrue(battle._flush_landing_observation())
        self.assertEqual([22.0], battle._pending_landing_impacts)
        self.assertEqual([
            'pose', ('landing', 18.5),
            'pose', ('landing', 18.5)], calls)

    def test_cross_heading_steep_slope_uses_copied_slide_law(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        descriptor = _Descriptor()
        battle._ground_y = lambda x, unused_z, unused_hint=0.0, **unused: -1.2 * x

        battle._ground_pitch((0.0, 0.0, 0.0), 0.0, descriptor)
        position = battle._apply_slope_slide(
            (0.0, 0.0, 0.0), 0.0, 0.1)

        self.assertAlmostEqual(
            math.atan(1.2), abs(battle._local_roll), places=6)
        self.assertGreater(abs(battle._local_roll), 0.61)
        self.assertGreater(battle._local_slide_speed, 0.0)
        self.assertGreater(position[0], 0.0)
        self.assertAlmostEqual(-1.2 * position[0], position[1], places=6)

    def test_cross_slope_slide_rejects_a_discontinuous_candidate_plane(self):
        battle = BattleRuntime(_runtime())
        descriptor = _Descriptor()
        calls = [0]

        def discontinuous_ground(x, unused_z, unused_hint=0.0, **unused):
            calls[0] += 1
            layer = 0.0 if calls[0] <= 5 else 1.0
            return -1.2 * x + layer

        battle._ground_y = discontinuous_ground
        battle._ground_pitch((0.0, 0.0, 0.0), 0.0, descriptor)

        position = battle._apply_slope_slide(
            (0.0, 0.0, 0.0), 0.0, 0.1)

        self.assertEqual((0.0, 0.0, 0.0), position)
        self.assertGreater(battle._local_slide_speed, 0.0)

    def test_diagonal_ground_plane_matches_bigworld_ypr_normal(self):
        battle = BattleRuntime(_runtime())
        forward_grade = 0.8
        right_grade = 0.6
        battle._ground_y = lambda x, z, unused_hint=0.0, **unused: (
            forward_grade * z + right_grade * x)

        battle._ground_pitch(
            (0.0, 0.0, 0.0), 0.0, _Descriptor())

        expected_pitch = -math.atan(forward_grade)
        expected_roll = math.atan2(
            right_grade, math.sqrt(1.0 + forward_grade ** 2))
        self.assertAlmostEqual(expected_pitch, battle._local_pitch)
        self.assertAlmostEqual(expected_roll, battle._local_roll)
        self.assertAlmostEqual(
            battle._local_surface_up_cosine,
            math.cos(battle._local_pitch) * math.cos(battle._local_roll))

    def test_airborne_slope_drift_is_carried_without_new_ground_slide(self):
        battle = BattleRuntime(_runtime())
        battle._local_airborne = True
        battle._local_slide_speed = 4.0
        battle._local_air_lateral = (2.0, -1.0)

        position = battle._apply_slope_slide(
            (0.0, 10.0, 0.0), 0.0, 0.1)

        self.assertEqual((0.2, 10.0, -0.1), position)
        self.assertEqual(0.0, battle._local_slide_speed)
        self.assertEqual((1.99, -0.995), battle._local_air_lateral)

    def test_airborne_lateral_carry_cannot_cross_the_arena_edge(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._arena_bounds = (-300.0, -300.0, 300.0, 300.0)
        battle._local_airborne = True
        battle._local_slide_speed = 4.0
        battle._local_air_lateral = (2.0, 0.0)
        entity = _Vehicle(
            10, _Descriptor(), _Vector(), (0, 0, 0), {'health': 500})

        position = battle._apply_slope_slide(
            (298.49, 10.0, 0.0), 0.0, 0.1, entity)

        self.assertEqual((298.49, 10.0, 0.0), position)
        self.assertEqual(0.0, battle._local_slide_speed)
        self.assertEqual((1.99, 0.0), battle._local_air_lateral)
        self.assertEqual('arena', battle._local_motion_kinds)

    def test_cross_slope_slide_carries_off_a_cliff_with_ground_plane(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(
            10, _Descriptor(), _Vector(), (0, 0, 0), {'health': 500})
        battle._ground_y = lambda x, unused_z, unused_hint=0.0, \
            **unused: -0.8 * x
        battle._ground_pitch((0.0, 0.0, 0.0), 0.0,
                             entity.typeDescriptor)
        battle._sample_ground_plane = mock.Mock(return_value=None)
        battle._terrain_support = mock.Mock(return_value=(-20.0, None))
        battle._motion_is_clear = mock.Mock(return_value=True)

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'vehicle_physics.slope_slide_speed', return_value=15.0):
            position = battle._apply_slope_slide(
                (0.0, 0.0, 0.0), 0.0, 0.1, entity)

        self.assertGreater(position[0], 0.0)
        self.assertLess(position[1], 0.0)
        self.assertTrue(battle._local_airborne)
        self.assertLess(battle._local_vertical_speed, 0.0)
        self.assertGreater(battle._local_air_lateral[0], 0.0)
        battle._terrain_support.assert_called_once()

    def test_cross_slope_slide_keeps_near_multipoint_gap_support(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(
            10, _Descriptor(), _Vector(), (0, 0, 0), {'health': 500})
        battle._ground_y = lambda x, unused_z, unused_hint=0.0, \
            **unused: -0.8 * x
        battle._ground_pitch((0.0, 0.0, 0.0), 0.0,
                             entity.typeDescriptor)
        battle._sample_ground_plane = mock.Mock(return_value=None)
        battle._terrain_support = mock.Mock(return_value=(-0.16, None))
        battle._motion_is_clear = mock.Mock(return_value=True)

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'vehicle_physics.slope_slide_speed', return_value=2.0):
            position = battle._apply_slope_slide(
                (0.0, 0.0, 0.0), 0.0, 0.1, entity)

        self.assertAlmostEqual(0.2, position[0])
        self.assertAlmostEqual(-0.16, position[1])
        self.assertFalse(battle._local_airborne)
        battle._terrain_support.assert_called_once()

    def test_cross_slope_slide_cannot_bypass_horizontal_collision(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(
            10, _Descriptor(), _Vector(), (0, 0, 0), {'health': 500})
        battle._local_slope_tangent = 0.8
        battle._local_downhill = (1.0, 0.0, 0.0)
        battle._local_slide_speed = 2.0
        battle._ground_y = lambda *unused, **unused_kw: -0.1
        battle._motion_is_clear = mock.Mock(return_value=False)
        battle._local_motion_soft_block = False

        position = battle._apply_slope_slide(
            (0.0, 0.0, 0.0), 0.0, 0.1, entity)

        self.assertEqual((0.0, 0.0, 0.0), position)
        self.assertEqual(0.0, battle._local_slide_speed)
        battle._motion_is_clear.assert_called_once()
        self.assertNotIn(
            'allow_crush_drive',
            battle._motion_is_clear.call_args.kwargs)
        self.assertEqual(
            0.0, battle._motion_is_clear.call_args.kwargs['hull_yaw'])
        unused_entity, unused_position, slide_yaw, slide_speed, step = (
            battle._motion_is_clear.call_args[0])
        self.assertAlmostEqual(math.pi * 0.5, slide_yaw)
        self.assertGreater(slide_speed, 0.0)
        self.assertEqual(0.1, step)

    def test_cross_slope_pending_skin_keeps_slide_for_a_later_tick(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(
            10, _Descriptor(), _Vector(), (0, 0, 0), {'health': 500})
        battle._local_slope_tangent = 0.8
        battle._local_downhill = (1.0, 0.0, 0.0)
        battle._local_slide_speed = 2.0
        battle._ground_y = lambda *unused, **unused_kw: -0.1
        battle._local_motion_soft_block = True
        battle._motion_is_clear = mock.Mock(return_value=False)

        position = battle._apply_slope_slide(
            (0.0, 0.0, 0.0), 0.0, 0.1, entity)

        self.assertEqual((0.0, 0.0, 0.0), position)
        self.assertGreater(battle._local_slide_speed, 0.0)

    def test_drive_pitch_skips_bridge_deck_above_the_hull(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar

        def collision(unused_space, start, unused_end, unused_mask):
            # First see an overhead bridge; the copied probe restarts below it
            # and then reaches the drivable terrain.
            if start.y > 7.5:
                return (_Vector(start.x, 8.0, start.z),)
            return (_Vector(start.x, 1.0 if start.z > 0 else 0.0,
                            start.z),)

        runtime.bigworld.wg_collideSegment = collision

        self.assertAlmostEqual(
            -math.atan2(1.0, 4.0),
            battle._drive_pitch((0.0, 0.0, 0.0), 0.0))

    def test_drive_pitch_median_rejects_one_frame_geometry_spike(self):
        battle = BattleRuntime(_runtime())
        readings = iter((0.2, 0.2, 0.9, 0.2, 0.2))
        battle._drive_pitch = lambda *unused: next(readings)

        values = [battle._smoothed_drive_pitch((0, 0, 0), 0.0)
                  for unused in range(5)]

        self.assertLess(max(values), 0.2)
        self.assertAlmostEqual(0.19375, values[-1])

    def test_drive_pitch_sign_accelerates_neutral_coast_downhill(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar

        def collision(unused_space, start, unused_end, unused_mask):
            return (_Vector(start.x, -0.3 * start.z, start.z),)

        runtime.bigworld.wg_collideSegment = collision
        downhill = uphill = None
        for unused in range(8):
            downhill = battle._smoothed_drive_pitch(
                (0.0, 0.0, 0.0), 0.0)
        battle._local_drive_pitch_history = None
        battle._local_smooth_drive_pitch = 0.0
        for unused in range(8):
            uphill = battle._smoothed_drive_pitch(
                (0.0, 0.0, 0.0), math.pi)

        self.assertGreater(downhill, 0.0)
        self.assertLess(uphill, 0.0)
        # A parkable descent now brakes on coast like the flat, so the sign
        # contract shows through gravity's offset against the brake share.
        params = dict(vehicle_physics._DEFAULTS)
        flat = vehicle_physics.longitudinal_step(
            params, 5.0, 0.0, False, 0.0, 0.1)
        self.assertGreater(vehicle_physics.longitudinal_step(
            params, 5.0, 0.0, False, downhill, 0.1), flat)
        self.assertLess(vehicle_physics.longitudinal_step(
            params, 5.0, 0.0, False, uphill, 0.1), flat)

    def test_landing_combines_lateral_impact_and_retains_skid(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._records = {'player:1': {
            'engine_id': 10, 'state': {'health': 500, 'alive': True},
            'kind': 'player', 'network_id': 1, 'local': True}}
        battle._local_air_lateral = (9.0, 0.0)

        damage = battle._apply_landing_impact(entity, 6.0)

        self.assertGreater(damage, 0)
        self.assertEqual((0.0, 0.0), battle._local_air_lateral)
        self.assertEqual(9.0, battle._local_slide_speed)

    def test_relative_gun_tracking_uses_delta_and_stop_uses_hull_yaw(self):
        owner = types.SimpleNamespace(
            local_pose=lambda: ((100.0, 5.0, 200.0), 0.5),
            local_stabilised_position=lambda: (101.0, 6.0, 202.0),
            client=types.SimpleNamespace(send_input=mock.Mock(return_value=True)))
        owner.shoot = mock.Mock(return_value=True)
        owner._echo_local_gun_angles = mock.Mock(return_value=True)
        sender = _LANInputSender(owner)

        sender.send_avatar_input(1, 'track_relative', {
            'point': _Vector(10.0, 2.0, 20.0)})
        self.assertAlmostEqual(math.atan2(10.0, 20.0), sender.aim_yaw)
        self.assertAlmostEqual(-math.atan2(2.0, math.sqrt(500.0)),
                               sender.gun_pitch)
        self.assertAlmostEqual(sender.gun_pitch, sender.aim_pitch)
        self.assertEqual((111.0, 8.0, 222.0), sender.aim_point)
        owner._echo_local_gun_angles.assert_called_once_with()

        sender.send_avatar_input(1, 'stop_tracking', {
            'turret_yaw': 0.25, 'gun_pitch': -0.1})
        self.assertAlmostEqual(0.75, sender.aim_yaw)
        self.assertAlmostEqual(-0.1, sender.gun_pitch)
        self.assertAlmostEqual(-0.1, sender.aim_pitch)
        self.assertIsNone(sender.aim_point)
        self.assertEqual(
            [mock.call(), mock.call(0.25, -0.1)],
            owner._echo_local_gun_angles.call_args_list)

    def test_native_cruise_flags_preserve_r_f_throttle_presets(self):
        send_input = mock.Mock(return_value=True)
        owner = types.SimpleNamespace(
            local_pose=lambda: ((0.0, 0.0, 0.0), 0.0),
            client=types.SimpleNamespace(send_input=send_input))
        sender = _LANInputSender(owner)

        # Exact #1513 emits full manual W first. If R was armed while W was
        # held, releasing W then emits FORWARD | CRUISE_CONTROL25; the native
        # PlayerAvatar and HUD retain ownership of that pending preset.
        sender.send_avatar_input(1, 'move', {'flags': 1})
        sender.send_avatar_input(1, 'move', {'flags': 1 | 32})
        self.assertEqual(
            [1.0, 0.25],
            [call.args[0] for call in send_input.call_args_list])

        for flags, throttle in (
                (1 | 16, 0.5), (1, 1.0),
                (2 | 16, -0.5), (2, -1.0), (0, 0.0)):
            sender.send_avatar_input(1, 'move', {'flags': flags})
            self.assertEqual(throttle, sender.forward)

    def test_cruise_mode_fallback_matches_native_mode_values(self):
        owner = types.SimpleNamespace(
            local_pose=lambda: ((0.0, 0.0, 0.0), 0.0),
            client=types.SimpleNamespace(
                send_input=mock.Mock(return_value=True)))
        sender = _LANInputSender(owner)

        for mode, throttle in (
                (-2, -1.0), (-1, -0.5), (0, 0.0),
                (1, 0.25), (2, 0.5), (3, 1.0)):
            sender.send_avatar_input(1, 'cruise', {'mode': mode})
            self.assertEqual(throttle, sender.forward)

    def test_local_gun_echo_updates_packed_server_angle_from_native_rotator(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.gunRotator = types.SimpleNamespace(
            turretYaw=0.35, gunPitch=-0.08)
        battle._binding = mock.Mock()
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._local_yaw = -0.2

        self.assertTrue(battle._echo_local_gun_angles())

        args = battle._binding.update_vehicle_aim.call_args[0]
        self.assertEqual(10, args[0])
        self.assertAlmostEqual(-0.2, args[1])
        self.assertAlmostEqual(0.15, args[2])
        self.assertAlmostEqual(-0.08, args[3])

    def test_local_snapshot_never_rewinds_native_vehicle_physics(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle.state = 'running'
        battle._binding = mock.Mock()
        entity = _Vehicle(10, _Descriptor(), _Vector(10, 0, 10), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._local_position = (10.0, 0.0, 10.0)
        battle._local_yaw = 0.0
        battle._records = {
            'player:1': {'engine_id': 10, 'state': {'health': 500},
                         'kind': 'player', 'network_id': 1, 'local': True}}

        battle._update_entity({
            'entity': 'player:1', 'kind': 'player', 'id': 1,
            'pose': {'x': 12.0, 'y': 0.0, 'z': 10.0, 'yaw': 0.1},
            'state': {'health': 500}})
        battle._binding.drive_vehicle.assert_not_called()

        battle._update_entity({
            'entity': 'player:1', 'kind': 'player', 'id': 1,
            'pose': {'x': 20.0, 'y': 0.0, 'z': 10.0, 'yaw': 0.2},
            'state': {'health': 500}})
        battle._binding.drive_vehicle.assert_not_called()

    def test_authority_applies_copied_bot_pose_to_remote_filter(self):
        battle = BattleRuntime(_runtime())
        battle._binding = mock.Mock()
        battle._records = {
            'bot:17': {'engine_id': 11, 'kind': 'bot', 'network_id': 17,
                       'ready': True, 'tombstone': False}}

        self.assertTrue(battle._apply_authority_bot_poses([{
            'id': 17, 'alive': True, 'x': 7.0, 'y': 2.0, 'z': 9.0,
            'yaw': 0.75, 'pitch': 0.2, 'roll': -0.3,
            'aim_yaw': 0.9, 'gun_pitch': -0.1, 'siege_state': 1}]))

        pose_call = battle._binding.set_vehicle_pose.call_args
        self.assertEqual(11, pose_call[0][0])
        self.assertEqual((7.0, 2.0, 9.0), tuple(pose_call[0][1]))
        self.assertEqual((-0.3, 0.2, 0.75), pose_call[0][2])
        battle._binding.update_vehicle_aim.assert_called_once_with(
            11, 0.75, 0.9, -0.1)
        collision_pose = battle._records['bot:17'][
            'projectile_collision_pose']
        self.assertEqual((7.0, 2.0, 9.0), (
            collision_pose['x'], collision_pose['y'], collision_pose['z']))
        self.assertEqual((0.75, 0.2, -0.3), (
            collision_pose['yaw'], collision_pose['pitch'],
            collision_pose['roll']))
        self.assertAlmostEqual(0.15, collision_pose['turret_yaw'])
        self.assertEqual(-0.1, collision_pose['gun_pitch'])
        self.assertEqual(1, collision_pose['siege_state'])

    def test_hidden_worker_authority_pose_skips_track_presentation(self):
        battle = BattleRuntime(_runtime())
        battle._worker_mode = True
        battle._binding = mock.Mock()
        battle._remote_factory = mock.Mock()
        battle._records = {
            'bot:17': {'engine_id': 11, 'kind': 'bot', 'network_id': 17,
                       'ready': True, 'tombstone': False}}

        self.assertTrue(battle._apply_authority_bot_poses([{
            'id': 17, 'alive': True, 'health': 500,
            'x': 7.0, 'y': 2.0, 'z': 9.0, 'speed': 6.0,
            'yaw': 0.75, 'aim_yaw': 0.9, 'gun_pitch': -0.1}]))

        battle._binding.set_vehicle_pose.assert_called_once()
        battle._binding.update_vehicle_aim.assert_called_once_with(
            11, 0.75, 0.9, -0.1)
        battle._remote_factory.get.assert_not_called()

    def test_hidden_worker_scans_human_tree_contacts(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._worker_mode = True
        battle._avatar = runtime.bigworld.avatar
        battle.client = types.SimpleNamespace(
            is_bot_authority=lambda: True)
        battle._destructibles = mock.Mock()
        descriptor = _Descriptor()
        battle._resolve_player_descriptor = mock.Mock(
            return_value=descriptor)
        battle._player_tree_destructible_scan_due = mock.Mock(
            return_value=True)
        state = {
            'id': 3, 'world_pose': True, 'alive': True,
            'x': 7.0, 'y': 2.0, 'z': 9.0,
            'yaw': 0.75, 'speed': 6.5}

        self.assertEqual(
            1, battle._scan_authority_player_trees([state], 10.0))

        battle._resolve_player_descriptor.assert_called_once_with(state)
        call = battle._destructibles._fell_trees_near.call_args[0]
        self.assertEqual(7, call[0])
        self.assertEqual((7.0, 2.0, 9.0), tuple(call[1]))
        self.assertEqual(0.75, call[2])
        self.assertEqual(6.5, call[3])
        self.assertIs(descriptor, call[4])

    def test_visible_client_never_commits_human_tree_contacts(self):
        battle = BattleRuntime(_runtime())
        battle._worker_mode = False
        battle.client = types.SimpleNamespace(
            is_bot_authority=lambda: False)
        battle._destructibles = mock.Mock()

        self.assertEqual(0, battle._scan_authority_player_trees([{
            'id': 3, 'world_pose': True, 'alive': True,
            'x': 7.0, 'y': 2.0, 'z': 9.0,
            'yaw': 0.75, 'speed': 6.5}], 10.0))
        battle._destructibles._fell_trees_near.assert_not_called()

    def test_authority_bot_motion_notifies_destructibles_before_pose(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._worker_mode = True
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        battle._destructibles = mock.Mock()
        descriptor = _Descriptor()
        entity = _Vehicle(11, descriptor, _Vector(), (0, 0, 0),
                          {'health': 500})
        battle._server_entity = mock.Mock(return_value=entity)
        battle._records = {
            'bot:17': {'engine_id': 11, 'kind': 'bot', 'network_id': 17,
                       'ready': True, 'tombstone': False}}
        battle._bot_destructible_samples[17] = (
            runtime.bigworld.now, (7.0, 2.0, 9.0))

        def set_vehicle_pose(*unused_args, relax_time=None, now=None):
            battle._destructibles._fell_trees_near.assert_called_once()

        battle._binding.set_vehicle_pose.side_effect = set_vehicle_pose

        self.assertTrue(battle._apply_authority_bot_poses([{
            'id': 17, 'alive': True, 'x': 7.0, 'y': 2.0, 'z': 9.0,
            'yaw': 0.75, 'speed': 6.5,
            'aim_yaw': 0.9, 'gun_pitch': -0.1}]))

        call = battle._destructibles._fell_trees_near.call_args[0]
        self.assertEqual(7, call[0])
        self.assertEqual((7.0, 2.0, 9.0), tuple(call[1]))
        self.assertEqual(0.75, call[2])
        self.assertEqual(6.5, call[3])
        self.assertIs(descriptor, call[4])

    def test_stopped_authority_bot_keeps_registration_scan_phase(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._worker_mode = True
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        battle._destructibles = mock.Mock()
        descriptor = _Descriptor()
        entity = _Vehicle(11, descriptor, _Vector(), (0, 0, 0),
                          {'health': 500})
        battle._server_entity = mock.Mock(return_value=entity)
        battle._records = {
            'bot:17': {'engine_id': 11, 'kind': 'bot', 'network_id': 17,
                       'ready': True, 'tombstone': False}}
        state = {
            'id': 17, 'alive': True, 'x': 7.0, 'y': 2.0, 'z': 9.0,
            'yaw': 0.75, 'speed': 0.0,
            'aim_yaw': 0.9, 'gun_pitch': -0.1}

        runtime.bigworld.now = 10.0
        self.assertTrue(battle._apply_authority_bot_poses([state]))
        battle._destructibles._fell_trees_near.assert_not_called()
        first_deadline = battle._bot_destructible_samples[17][0]
        self.assertGreater(first_deadline, 10.0)
        self.assertLessEqual(first_deadline, 10.1)

        runtime.bigworld.now = first_deadline + 0.001
        self.assertTrue(battle._apply_authority_bot_poses([state]))

        call = battle._destructibles._fell_trees_near.call_args[0]
        self.assertEqual(7, call[0])
        self.assertEqual((7.0, 2.0, 9.0), tuple(call[1]))
        self.assertEqual(0.0, call[3])
        self.assertIs(descriptor, call[4])
        next_deadline = battle._bot_destructible_samples[17][0]
        self.assertAlmostEqual(runtime.bigworld.now + 0.5, next_deadline)

    def test_authority_bot_destructible_budget_is_render_rate_independent(self):
        totals = {}
        for fps in (40, 60, 120):
            with self.subTest(fps=fps):
                runtime = _runtime()
                battle = BattleRuntime(runtime)
                battle._worker_mode = True
                battle._avatar = runtime.bigworld.avatar
                battle._binding = mock.Mock()
                battle._destructibles = mock.Mock()
                descriptor = _Descriptor()
                entity = _Vehicle(
                    11, descriptor, _Vector(), (0, 0, 0), {'health': 500})
                battle._server_entity = mock.Mock(return_value=entity)
                battle._records = dict(
                    ('bot:%d' % bot_id, {
                        'engine_id': 1000 + bot_id, 'kind': 'bot',
                        'network_id': bot_id, 'ready': True,
                        'tombstone': False,
                    })
                    for bot_id in range(11, 40))
                dt = 1.0 / float(fps)
                later_counts = []
                for frame in range(fps * 2):
                    runtime.bigworld.now = 10.0 + (frame + 1) * dt
                    before = battle._destructibles._fell_trees_near.call_count
                    states = tuple({
                        'id': bot_id, 'alive': True,
                        'x': float((bot_id - 11) * 12), 'y': 0.0,
                        'z': -100.0 + 14.0 * (frame + 1) * dt,
                        'yaw': 0.0, 'speed': 14.0,
                        'aim_yaw': 0.0, 'gun_pitch': 0.0,
                    } for bot_id in range(11, 40))
                    self.assertTrue(
                        battle._apply_authority_bot_poses(states))
                    count = (
                        battle._destructibles._fell_trees_near.call_count -
                        before)
                    if frame == 0:
                        self.assertEqual(0, count)
                    else:
                        later_counts.append(count)

                self.assertEqual(
                    fps * 2 * 29,
                    battle._binding.set_vehicle_pose.call_count)
                self.assertLess(max(later_counts), 29)
                totals[fps] = (
                    battle._destructibles._fell_trees_near.call_count)

        self.assertLessEqual(
            max(totals.values()) - min(totals.values()), 29 * 2)

    def test_authority_bot_destructible_budget_resamples_after_three_metres(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._worker_mode = True
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        battle._destructibles = mock.Mock()
        descriptor = _Descriptor()
        entity = _Vehicle(
            11, descriptor, _Vector(), (0, 0, 0), {'health': 500})
        battle._server_entity = mock.Mock(return_value=entity)
        battle._records = {
            'bot:17': {'engine_id': 11, 'kind': 'bot', 'network_id': 17,
                       'ready': True, 'tombstone': False}}

        runtime.bigworld.now = 10.0
        self.assertTrue(battle._apply_authority_bot_poses([{
            'id': 17, 'x': 0.0, 'y': 0.0, 'z': 0.0,
            'yaw': 0.0, 'speed': 35.0,
            'aim_yaw': 0.0, 'gun_pitch': 0.0}]))
        runtime.bigworld.now = 10.001
        self.assertTrue(battle._apply_authority_bot_poses([{
            'id': 17, 'x': 0.0, 'y': 0.0, 'z': 2.99,
            'yaw': 0.0, 'speed': 35.0,
            'aim_yaw': 0.0, 'gun_pitch': 0.0}]))
        self.assertEqual(
            0, battle._destructibles._fell_trees_near.call_count)
        runtime.bigworld.now = 10.002
        self.assertTrue(battle._apply_authority_bot_poses([{
            'id': 17, 'x': 0.0, 'y': 0.0, 'z': 3.01,
            'yaw': 0.0, 'speed': 35.0,
            'aim_yaw': 0.0, 'gun_pitch': 0.0}]))
        self.assertEqual(
            1, battle._destructibles._fell_trees_near.call_count)

    def test_canonical_fragile_preserves_shot_damage_bit(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._destructibles = mock.Mock()
        battle._destructibles.is_isolated_1513.return_value = False
        event = {
            'destructible_kind': 'fragile',
            'chunk_id': 3, 'item_index': 9,
            'x': 1.0, 'y': 2.0, 'z': 3.0,
            'fall_yaw': 0.0, 'speed': 0.0,
            'is_shot': True}
        authority = types.ModuleType(
            'gui.mods.offline_lan_0922.destructibles_authority')
        authority.is_destroyed = mock.Mock(return_value=False)
        authority.destroy_fragile = mock.Mock(return_value=True)
        package = sys.modules['gui.mods.offline_lan_0922']

        with mock.patch.dict(sys.modules, {
                'gui.mods.offline_lan_0922.destructibles_authority':
                authority}), mock.patch.object(
                    package, 'destructibles_authority', authority,
                    create=True):
            self.assertTrue(battle._apply_destructible_event(event))

        args = authority.destroy_fragile.call_args[0]
        self.assertEqual((7, 3, 9), args[:3])
        self.assertEqual((1.0, 2.0, 3.0), tuple(args[3]))
        self.assertIs(True, args[4])
        battle._destructibles.note_destroyed.assert_called_once_with(
            'fragile', 3, 9, None, runtime.bigworld.now)
        battle._destructibles.clear_local_prediction.assert_called_once_with(
            ((3, 9, None),))

        invalid = dict(event)
        del invalid['is_shot']
        with mock.patch.dict(sys.modules, {
                'gui.mods.offline_lan_0922.destructibles_authority':
                authority}), mock.patch.object(
                    package, 'destructibles_authority', authority,
                    create=True), self.assertRaisesRegex(
                    RuntimeError, 'shot flag is invalid'):
            battle._apply_destructible_event(invalid)

    def test_canonical_tree_activates_foliage_on_first_event_and_echo(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._destructibles = mock.Mock()
        battle._destructibles.is_isolated_1513.return_value = False
        battle._foliage = mock.Mock()
        battle._foliage.activate_fallen_tree.side_effect = [True, False]
        event = {
            'destructible_kind': 'tree',
            'chunk_id': 3, 'item_index': 9,
            'x': 1.0, 'y': 2.0, 'z': 3.0,
            'fall_yaw': 0.75, 'speed': 2.0,
            'is_shot': False}
        authority = types.ModuleType(
            'gui.mods.offline_lan_0922.destructibles_authority')
        authority.is_destroyed = mock.Mock(side_effect=[False, True])
        authority.destroy_tree = mock.Mock(return_value=True)
        package = sys.modules['gui.mods.offline_lan_0922']

        with mock.patch.dict(sys.modules, {
                'gui.mods.offline_lan_0922.destructibles_authority':
                authority}), mock.patch.object(
                    package, 'destructibles_authority', authority,
                    create=True):
            self.assertTrue(battle._apply_destructible_event(event))
            self.assertFalse(battle._apply_destructible_event(event))

        authority.destroy_tree.assert_called_once()
        args = authority.destroy_tree.call_args[0]
        self.assertEqual((7, 3, 9), args[:3])
        self.assertEqual(0.75, args[3])
        self.assertEqual(2.0, args[4])
        self.assertEqual((1.0, 2.0, 3.0), tuple(args[5]))
        self.assertEqual(
            [mock.call(3, 9), mock.call(3, 9)],
            battle._foliage.activate_fallen_tree.call_args_list)
        battle._destructibles.note_destroyed.assert_called_once_with(
            'tree', 3, 9, None, runtime.bigworld.now)

    def test_fallen_tree_foliage_follows_native_direction_and_angle(self):
        class NativeTreeMatrix(object):
            def __init__(self, translation):
                self.translation = _Vector(translation)

            @staticmethod
            def applyVector(value):
                value = _Vector(value)
                return _Vector(
                    3.0 * value.y,
                    4.0 * value.y + 0.5 * value.z,
                    2.0 * value.x + 0.5 * value.z)

            def applyPoint(self, value):
                return self.translation + self.applyVector(value)

        runtime = _runtime()
        current_matrix = [NativeTreeMatrix((1.0, 2.0, 3.0))]
        runtime.math.Matrix = lambda value=None: value
        runtime.bigworld.wg_getChunkMatrix = lambda space, chunk: \
            types.SimpleNamespace(translation=_Vector(100.0, 2.0, 200.0))
        runtime.bigworld.wg_getDestructibleMatrix = \
            lambda space, chunk, item: current_matrix[0]
        bodies = [{
            'spaceID': 7, 'chunkID': 3, 'destrIndex': 9,
        }]
        runtime.area_destructibles = types.SimpleNamespace(
            g_destructiblesAnimator=types.SimpleNamespace(
                _DestructiblesAnimator__bodies=bodies),
            g_destructiblesManager=types.SimpleNamespace(
                forceNoAnimation=False,
                _DestructiblesManager__loadedChunkIDs={3: 10},
                _DestructiblesManager__destructiblesWaitDestroy={}))
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._foliage = mock.Mock()
        battle._foliage.refreshing_fallen_tree_wires.return_value = ((3, 9),)
        battle._foliage.fallen_tree_profile.return_value = (
            (0.0, 5.0, 0.0), (2.0, 5.0, 1.0))
        battle._foliage.update_fallen_tree_pose.return_value = True

        self.assertTrue(battle._refresh_fallen_tree_foliage(1.0))
        first = battle._foliage.update_fallen_tree_pose.call_args[0]
        self.assertEqual((3, 9), first[:2])
        self.assertEqual((116.0, 24.0, 203.0), first[2])
        self.assertEqual((
            (0.0, 0.0, 4.0),
            (15.0, 20.0, 0.0),
            (0.0, 0.5, 0.5),
        ), first[3])
        self.assertIn((3, 9), battle._fallen_tree_foliage_seen_bodies)

        current_matrix[0] = NativeTreeMatrix((2.0, 1.0, 4.0))
        bodies[:] = []
        self.assertFalse(battle._refresh_fallen_tree_foliage(1.1))
        battle._foliage.settle_fallen_tree.assert_called_once_with(3, 9)
        self.assertNotIn((3, 9), battle._fallen_tree_foliage_seen_bodies)

    def test_fallen_tree_foliage_waits_for_queued_chunk_before_settling(self):
        class NativeTreeMatrix(object):
            def __init__(self, translation):
                self.translation = _Vector(translation)

            @staticmethod
            def applyVector(value):
                return _Vector(value)

            def applyPoint(self, value):
                return self.translation + _Vector(value)

        runtime = _runtime()
        current_matrix = [NativeTreeMatrix((0.0, 0.0, 0.0))]
        runtime.math.Matrix = lambda value=None: value
        runtime.bigworld.wg_getChunkMatrix = lambda space, chunk: \
            types.SimpleNamespace(translation=_Vector())
        runtime.bigworld.wg_getDestructibleMatrix = \
            lambda space, chunk, item: current_matrix[0]
        manager = types.SimpleNamespace(
            forceNoAnimation=True,
            _DestructiblesManager__loadedChunkIDs={},
            _DestructiblesManager__destructiblesWaitDestroy={3: [object()]})
        runtime.area_destructibles = types.SimpleNamespace(
            g_destructiblesAnimator=types.SimpleNamespace(
                _DestructiblesAnimator__bodies=[]),
            g_destructiblesManager=manager)
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._foliage = mock.Mock()
        battle._foliage.refreshing_fallen_tree_wires.return_value = ((3, 9),)
        battle._foliage.fallen_tree_profile.return_value = (
            (0.0, 5.0, 0.0), (2.0, 5.0, 1.0))
        battle._foliage.update_fallen_tree_pose.return_value = True

        self.assertFalse(battle._refresh_fallen_tree_foliage(1.0))
        battle._foliage.update_fallen_tree_pose.assert_not_called()
        battle._foliage.settle_fallen_tree.assert_not_called()

        manager._DestructiblesManager__destructiblesWaitDestroy.clear()
        manager._DestructiblesManager__loadedChunkIDs[3] = 10
        current_matrix[0] = NativeTreeMatrix((8.0, 1.0, -2.0))
        self.assertTrue(battle._refresh_fallen_tree_foliage(1.1))
        update = battle._foliage.update_fallen_tree_pose.call_args[0]
        self.assertEqual((8.0, 6.0, -2.0), update[2])
        battle._foliage.settle_fallen_tree.assert_called_once_with(3, 9)

    def test_fallen_tree_foliage_accepts_set_loaded_chunk_container(self):
        runtime = _runtime()
        runtime.bigworld.wg_getChunkMatrix = mock.Mock()
        runtime.area_destructibles = types.SimpleNamespace(
            g_destructiblesAnimator=types.SimpleNamespace(
                _DestructiblesAnimator__bodies=[]),
            g_destructiblesManager=types.SimpleNamespace(
                forceNoAnimation=False,
                _DestructiblesManager__loadedChunkIDs=set(),
                _DestructiblesManager__destructiblesWaitDestroy={}))
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._foliage = mock.Mock()
        battle._foliage.refreshing_fallen_tree_wires.return_value = ((3, 9),)

        self.assertFalse(battle._refresh_fallen_tree_foliage(1.0))
        runtime.bigworld.wg_getChunkMatrix.assert_not_called()
        battle._foliage.fallen_tree_profile.assert_not_called()

    def test_fallen_tree_body_disappearance_settles_before_native_query(self):
        runtime = _runtime()
        runtime.bigworld.wg_getChunkMatrix = mock.Mock()
        runtime.area_destructibles = types.SimpleNamespace(
            g_destructiblesAnimator=types.SimpleNamespace(
                _DestructiblesAnimator__bodies=[]),
            g_destructiblesManager=types.SimpleNamespace(
                forceNoAnimation=False,
                _DestructiblesManager__loadedChunkIDs={3},
                _DestructiblesManager__destructiblesWaitDestroy={}))
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._foliage = mock.Mock()
        battle._foliage.refreshing_fallen_tree_wires.return_value = ((3, 9),)
        battle._fallen_tree_foliage_seen_bodies.add((3, 9))

        self.assertFalse(battle._refresh_fallen_tree_foliage(1.0))
        battle._foliage.settle_fallen_tree.assert_called_once_with(3, 9)
        runtime.bigworld.wg_getChunkMatrix.assert_not_called()
        battle._foliage.fallen_tree_profile.assert_not_called()
        self.assertNotIn((3, 9), battle._fallen_tree_foliage_seen_bodies)

    def test_canonical_event_never_reenters_an_isolated_native_wire(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._destructibles = mock.Mock()
        battle._destructibles.is_isolated_1513.return_value = True
        event = {
            'destructible_kind': 'fragile',
            'chunk_id': 3, 'item_index': 9,
            'x': 1.0, 'y': 2.0, 'z': 3.0,
            'fall_yaw': 0.0, 'speed': 0.0,
            'is_shot': True}
        authority = types.ModuleType(
            'gui.mods.offline_lan_0922.destructibles_authority')
        authority.is_destroyed = mock.Mock(return_value=False)
        authority.destroy_fragile = mock.Mock(return_value=True)
        package = sys.modules['gui.mods.offline_lan_0922']

        with mock.patch.dict(sys.modules, {
                'gui.mods.offline_lan_0922.destructibles_authority':
                authority}), mock.patch.object(
                    package, 'destructibles_authority', authority,
                    create=True):
            self.assertFalse(battle._apply_destructible_event(event))

        authority.is_destroyed.assert_not_called()
        authority.destroy_fragile.assert_not_called()
        battle._destructibles.note_destroyed.assert_not_called()
        battle._destructibles.clear_local_prediction.assert_called_once_with(
            ((3, 9, None),))

    def test_canonical_tree_with_unsafe_descriptor_is_nonfatal(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._destructibles = mock.Mock()
        battle._destructibles.is_isolated_1513.return_value = False
        battle._destructibles.validate_tree_identity_1513.return_value = False
        event = {
            'destructible_kind': 'tree',
            'chunk_id': 3, 'item_index': 9,
            'x': 1.0, 'y': 2.0, 'z': 3.0,
            'fall_yaw': 0.0, 'speed': 6.0,
            'is_shot': False}
        authority = types.ModuleType(
            'gui.mods.offline_lan_0922.destructibles_authority')
        authority.is_destroyed = mock.Mock(return_value=False)
        authority.destroy_tree = mock.Mock(return_value=True)
        package = sys.modules['gui.mods.offline_lan_0922']

        with mock.patch.dict(sys.modules, {
                'gui.mods.offline_lan_0922.destructibles_authority':
                authority}), mock.patch.object(
                    package, 'destructibles_authority', authority,
                    create=True):
            self.assertFalse(battle._apply_destructible_event(event))

        battle._destructibles.validate_tree_identity_1513.assert_called_once_with(
            7, 3, 9)
        authority.is_destroyed.assert_not_called()
        authority.destroy_tree.assert_not_called()
        battle._destructibles.note_destroyed.assert_not_called()
        battle._destructibles.clear_local_prediction.assert_called_once_with(
            ((3, 9, None),))

    def test_server_disabled_destructibles_stop_the_sensor_once(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._start_message = {'round_id': 4}
        sensor = mock.Mock()
        battle._destructibles = sensor
        battle._bots = None
        message = {
            'round_id': 4, 'bot_authority_id': 0,
            'destructibles_disabled': True,
            'destructibles_disabled_reason': 'destructible_map_timeout',
        }

        with contextlib.redirect_stdout(io.StringIO()) as log:
            self.assertTrue(battle.on_roster(message))
            self.assertTrue(battle.on_roster(message))

        self.assertIsNone(battle._destructibles)
        sensor.set_event_sink.assert_called_once_with(None)
        sensor.reset.assert_called_once_with()
        sensor.set_catalog.assert_called_once_with(None)
        self.assertEqual(
            1, log.getvalue().count(
                'optional destructible interactions disabled for this round'))

    def test_disabled_snapshot_skips_canonical_destructible_replay(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._start_message = {'round_id': 4}
        sensor = mock.Mock()
        battle._destructibles = sensor
        battle._bots = None
        battle._sync = None
        battle._observe_projectile_message = mock.Mock()
        battle._reconcile_projectile_snapshot = mock.Mock()
        event = {
            'destructible_kind': 'fragile',
            'chunk_id': 3, 'item_index': 9,
            'x': 1.0, 'y': 2.0, 'z': 3.0,
            'fall_yaw': 0.0, 'speed': 0.0,
            'is_shot': True}

        with contextlib.redirect_stdout(io.StringIO()):
            battle.on_snapshot({
                'destructibles_disabled': True,
                'destructibles_disabled_reason':
                    'client_destructible_map_unavailable',
                'destructibles': [event],
            })

        self.assertEqual('running', battle.state)
        self.assertIsNone(battle._destructibles)
        sensor.set_event_sink.assert_called_once_with(None)

    def test_authority_updates_hidden_remote_through_private_registry(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        factory = RemoteVehicleFactory(
            runtime.bigworld, runtime.math, runtime.model_assembler, 7)
        battle._remote_factory = factory
        battle._binding = BigWorldVehicleBinding(
            runtime.bigworld, runtime.bigworld.avatar, runtime.constants,
            runtime.vehicles.VehicleDescr, runtime.encode_gun_angles,
            outfit_provider=lambda unused_descriptor: '',
            authority_entity_resolver=battle._server_entity)
        vehicle_id = factory.create(_Descriptor(), {
            'publicInfo': {'team': 2, 'name': 'Hidden Bot'},
            'health': 500, 'isCrewActive': True, 'gunAnglesPacked': 0},
            _Vector(), (0.0, 0.0, 0.0))
        vehicle = factory.get(vehicle_id)
        battle._records = {
            'bot:17': {
                'engine_id': vehicle_id, 'kind': 'bot', 'network_id': 17,
                'ready': True, 'tombstone': False}}

        try:
            self.assertIsNone(runtime.bigworld.entity(vehicle_id))
            self.assertTrue(battle._apply_authority_bot_poses([{
                'id': 17, 'alive': True, 'x': 7.0, 'y': 2.0, 'z': 9.0,
                'yaw': 0.75, 'aim_yaw': 0.9, 'gun_pitch': -0.1}]))

            self.assertEqual((7.0, 2.0, 9.0), tuple(vehicle.position))
            self.assertAlmostEqual(0.75, vehicle.yaw)
            self.assertAlmostEqual(0.9, vehicle._aim_yaw)
            self.assertAlmostEqual(-0.1, vehicle._gun_pitch)
            self.assertIsNone(runtime.bigworld.entity(vehicle_id))
        finally:
            factory.destroy_all()

    def test_authority_server_echo_cannot_rewind_presented_bot_pose(self):
        battle = BattleRuntime(_runtime())
        battle.state = 'running'
        battle._binding = mock.Mock()
        battle._bots = types.SimpleNamespace(is_authority=lambda: True)
        battle._records = {
            'bot:17': {
                'engine_id': 11, 'kind': 'bot', 'network_id': 17,
                'ready': True, 'local': False, 'tombstone': False,
                'state': {'team': 2, 'yaw': 0.75}}}

        battle._apply_sync_event({
            'type': 'update', 'entity': 'bot:17', 'kind': 'bot', 'id': 17,
            'state': {'team': 2, 'yaw': 0.60},
            'pose': {'x': 7.0, 'y': 2.0, 'z': 9.0, 'yaw': 0.60,
                     'aim_yaw': 0.8, 'gun_pitch': -0.1},
            'remote': True, 'interpolated': True})

        battle._binding.set_vehicle_pose.assert_not_called()
        self.assertEqual(0.60, battle._records['bot:17']['state']['yaw'])

    def test_enemy_spotting_controls_model_marker_and_ten_second_memory(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._local_descriptor = _Descriptor()
        battle._local_position = (0.0, 0.0, 0.0)
        local = _Vehicle(
            10, battle._local_descriptor, _Vector(), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities[10] = local
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._binding = mock.Mock()
        enemy = RemoteVehicle(
            1000, _Descriptor(), {
                'publicInfo': {'team': 2, 'name': 'Enemy'},
                'health': 500, 'isCrewActive': True,
                'gunAnglesPacked': 0},
            _Vector(100.0, 0.0, 0.0), (0.0, 0.0, 0.0),
            types.SimpleNamespace(Vector3=_Vector, Matrix=_Matrix))
        enemy.model = _Model()
        enemy.model.visible = False
        enemy.appearance.attach(enemy.model)
        enemy.isStarted = True
        enemy.inWorld = True
        runtime.bigworld.entities[1000] = enemy
        battle._remote_factory = types.SimpleNamespace(
            get=lambda entity_id: enemy if entity_id == 1000 else None)
        battle._records = {'bot:17': {
            'engine_id': 1000, 'kind': 'bot', 'network_id': 17,
            'ready': True, 'local': False, 'presentation': True,
            'tombstone': False, 'arena_added': True,
            'visual_started': False, 'spot_visible': False,
            'spot_until': 0.0, 'spot_next': 0.0,
            'state': {'team': 2, 'health': 500, 'alive': True}}}

        # Network id 17 owns the 0.40-second phase of the 2 Hz probe cycle.
        # The initial update must not put every enemy's native LOS work on the
        # same rendered frame.
        self.assertFalse(battle._update_spotting(10.0))
        self.assertTrue(battle._update_spotting(10.4))
        self.assertTrue(enemy.model.visible)
        battle._binding.start_vehicle_visual.assert_called_once_with(
            1000, True)
        self.assertEqual(1, len(battle._avatar.battle_events))
        events = battle._avatar.battle_events[0]
        self.assertEqual([0, 12], [event['eventType'] for event in events])
        self.assertEqual([1000, 1000],
                         [event['targetID'] for event in events])
        self.assertEqual(3, events[1]['details'])

        runtime.bigworld.wg_collideSegment = lambda *unused: (_Vector(),)
        self.assertFalse(battle._update_spotting(10.9))
        self.assertTrue(enemy.model.visible)
        self.assertFalse(battle._update_spotting(15.5))
        self.assertTrue(enemy.model.visible)
        self.assertTrue(battle._update_spotting(20.5))
        self.assertFalse(enemy.model.visible)
        battle._binding.stop_vehicle_visual.assert_called_once_with(
            1000, False)

        # Reacquiring the same target is presentation visibility, not a new
        # first-spot ribbon or detection sound.
        runtime.bigworld.wg_collideSegment = lambda *unused: None
        self.assertTrue(battle._update_spotting(20.9))
        self.assertEqual(1, len(battle._avatar.battle_events))

    def test_destroyed_enemy_wreck_does_not_require_spot_history(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._local_descriptor = _Descriptor()
        battle._local_position = (0.0, 0.0, 0.0)
        local = _Vehicle(
            10, battle._local_descriptor, _Vector(), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities[10] = local
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._binding = mock.Mock()
        enemy = RemoteVehicle(
            1000, _Descriptor(), {
                'publicInfo': {'team': 2, 'name': 'Enemy'},
                'health': 0, 'isCrewActive': True,
                'gunAnglesPacked': 0},
            _Vector(100.0, 0.0, 0.0), (0.0, 0.0, 0.0),
            types.SimpleNamespace(Vector3=_Vector, Matrix=_Matrix))
        enemy.model = _Model()
        enemy.model.visible = False
        enemy.appearance.attach(enemy.model)
        enemy.isStarted = True
        enemy.inWorld = True
        runtime.bigworld.entities[1000] = enemy
        battle._remote_factory = types.SimpleNamespace(
            get=lambda entity_id: enemy if entity_id == 1000 else None)
        battle._records = {'bot:17': {
            'engine_id': 1000, 'kind': 'bot', 'network_id': 17,
            'ready': True, 'local': False, 'presentation': True,
            'tombstone': False, 'arena_added': True,
            'visual_started': True, 'spot_visible': False,
            'spot_until': 0.0, 'spot_next': 0.0,
            'state': {'team': 2, 'health': 0, 'alive': False}}}

        battle._update_spotting(10.4)

        self.assertFalse(battle._records['bot:17']['spot_visible'])
        battle._binding.stop_vehicle_visual.assert_called_once_with(
            1000, False)
        self.assertTrue(enemy.model.visible)

    def test_full_state_update_does_not_hide_an_enemy_wreck(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        enemy = _Vehicle(
            1000, _Descriptor(), _Vector(100.0, 0.0, 0.0),
            (0.0, 0.0, 0.0), {'health': 0})
        enemy.model.visible = True
        battle._remote_factory = types.SimpleNamespace(
            get=lambda entity_id: enemy if entity_id == 1000 else None)
        record = {
            'engine_id': 1000, 'kind': 'bot', 'network_id': 17,
            'ready': True, 'local': False, 'presentation': True,
            'presentation_initialized': True, 'native_remote': True,
            'arena_added': True, 'visual_started': False,
            'spot_visible': False, 'spot_marker_visible': False,
            '_spot_presentation_signature': (False, False, True, False),
            'state': {'team': 2, 'health': 0, 'alive': False}}

        with mock.patch.object(battle, '_apply_siege_state'), \
                mock.patch.object(battle, '_apply_vehicle_statistics'), \
                mock.patch.object(battle, '_drain_event_journal'), \
                mock.patch.object(
                    battle, '_pending_combat_for_record', return_value=False), \
                mock.patch.object(battle, '_apply_health'):
            self.assertTrue(battle._materialize_record(record))

        self.assertTrue(enemy.model.visible)
        self.assertEqual([], enemy.shows)

    def test_full_state_update_preserves_far_team_spot_minimap(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        enemy = _Vehicle(
            1000, _Descriptor(), _Vector(600.0, 0.0, 0.0),
            (0.0, 0.0, 0.0), {'health': 500})
        enemy.model.visible = False
        battle._remote_factory = types.SimpleNamespace(
            get=lambda entity_id: enemy if entity_id == 1000 else None)
        record = {
            'engine_id': 1000, 'kind': 'bot', 'network_id': 17,
            'ready': True, 'local': False, 'presentation': True,
            'presentation_initialized': True, 'native_remote': True,
            'arena_added': True, 'visual_started': True,
            'world_marker_started': False, 'minimap_started': True,
            'spot_visible': False, 'spot_marker_visible': True,
            '_spot_presentation_signature': (False, True, False, True),
            'state': {'team': 2, 'health': 500, 'alive': True}}

        with mock.patch.object(battle, '_apply_siege_state'), \
                mock.patch.object(battle, '_apply_vehicle_statistics'), \
                mock.patch.object(battle, '_drain_event_journal'), \
                mock.patch.object(
                    battle, '_pending_combat_for_record', return_value=True):
            self.assertTrue(battle._materialize_record(record))

        self.assertFalse(record['spot_visible'])
        self.assertTrue(record['spot_marker_visible'])
        self.assertFalse(record['world_marker_started'])
        self.assertTrue(record['minimap_started'])
        battle._binding.start_vehicle_minimap.assert_not_called()
        battle._binding.stop_vehicle_minimap.assert_not_called()

    def test_spotting_leaves_the_outline_alone(self):
        """PyModel.visible writes one flag at model+0xC4 and never touches the
        scene key, so a spotting update must not disturb the edge.  It runs
        every tick for every enemy, and clearing there erased the outline the
        same frame the port had drawn it."""
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        enemy = RemoteVehicle(
            1000, _Descriptor(), {
                'publicInfo': {'team': 2, 'name': 'Enemy'},
                'health': 500, 'isCrewActive': True, 'gunAnglesPacked': 0},
            _Vector(100.0, 0.0, 0.0), (0.0, 0.0, 0.0), runtime.math)
        enemy.model = _Model()
        enemy.appearance.attach(enemy.model)
        enemy.bw_entity = types.SimpleNamespace(model=enemy.model)
        battle._remote_factory = types.SimpleNamespace(
            get=lambda entity_id: enemy if entity_id == 1000 else None)
        order = []
        original = enemy.appearance.changeVisibility

        def changeVisibility(visible):
            order.append('visibility')
            return original(visible)

        enemy.appearance.changeVisibility = changeVisibility
        runtime.bigworld.wgDelEdgeDetectEntity = (
            lambda entity: order.append('edge'))
        battle._outlined_engine_id = 1000
        battle._outlined_entity = enemy.bw_entity
        battle._outlined_vehicle = enemy
        battle._outlined_model = enemy.model
        record = {
            'engine_id': 1000, 'kind': 'bot', 'network_id': 17,
            'ready': True, 'local': False, 'presentation': True,
            'visual_started': True, 'spot_visible': True,
            'state': {'team': 2, 'health': 500, 'alive': True}}
        battle._records = {'bot:17': record}

        battle._set_record_spot_visibility(record, True)
        battle._set_record_spot_visibility(record, True)

        self.assertEqual(['visibility'], order)
        self.assertEqual(1000, battle._outlined_engine_id)

        battle._set_record_spot_visibility(record, False)

        self.assertEqual('edge', order[-1])
        self.assertIsNone(battle._outlined_engine_id)
        self.assertFalse(battle._outline_blocked)

    def test_the_targeting_report_never_reads_the_entity_attribute(self):
        """#1513's PyTarget.entity getter loads EntityPicker::pTarget_ and
        dereferences it at +0xD4 with no null check, so reading the attribute
        while nothing is picked faults on 0x000000D4 and kills the client.
        PyTarget's tp_call checks isFull, isHidden and the pointer before it
        returns the entity, and PlayerAvatar.handleKey reads the target that
        way."""
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        picked = []

        class _PyTarget(object):

            isEnabled = True
            isFull = False
            selectionFovDegrees = 1.0
            maxDistance = 710.0
            skeletonCheckEnabled = True

            @property
            def entity(self):
                raise AssertionError('PyTarget.entity faults on a null pick')

            def __call__(self):
                picked.append(True)
                return None

        runtime.bigworld.target = _PyTarget()
        battle._local_matrix = _Matrix()
        written = []
        with mock.patch.object(sys, 'stdout') as stdout:
            stdout.write = written.append
            self.assertTrue(battle._report_local_compound(100.0))
            self.assertFalse(battle._report_local_compound(101.0))

        self.assertEqual([True], picked)
        targeting = [line for line in written if 'TARGETING' in line]
        compound = [line for line in written if 'COMPOUND' in line]
        self.assertEqual(1, len(targeting))
        self.assertEqual(1, len(compound))
        self.assertIn('entity=None', targeting[0])

    def test_compound_report_repeats_only_for_a_bounded_signature_change(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)

        class _DiagnosticMatrix(object):
            def __init__(self):
                self.translation = _Vector()
                self.scale = 1.0

            def applyToAxis(self, index):
                axes = ((_Vector(1.0), _Vector(0.0, 1.0),
                         _Vector(0.0, 0.0, 1.0)))
                return axes[index].scale(self.scale)

        class _Target(object):
            isEnabled = True
            isFull = False
            selectionFovDegrees = 1.0
            maxDistance = 710.0
            skeletonCheckEnabled = True

            def __call__(self):
                return None

        matrix = _DiagnosticMatrix()
        target = _Target()
        runtime.bigworld.target = target
        battle._local_matrix = matrix
        written = []
        with mock.patch.object(sys, 'stdout') as stdout:
            stdout.write = written.append
            self.assertTrue(battle._report_local_compound(100.0))
            matrix.translation = _Vector(10.0, 0.0, 0.0)
            self.assertFalse(battle._report_local_compound(105.0))
            target.maxDistance = 720.0
            self.assertTrue(battle._report_local_compound(105.0))
            matrix.scale = 0.0
            self.assertTrue(battle._report_local_compound(110.0))
            for index in range(20):
                target.maxDistance = 800.0 + index
                battle._report_local_compound(115.0 + index * 5.0)

        targeting = [line for line in written if 'TARGETING' in line]
        compounds = [line for line in written if 'COMPOUND' in line]
        self.assertEqual(battle._COMPOUND_REPORT_LIMIT, len(targeting))
        self.assertEqual(battle._COMPOUND_REPORT_LIMIT, len(compounds))
        self.assertIn('axes=0.000/0.000/0.000', ''.join(compounds))

    def test_outline_diagnostics_are_slow_and_bounded(self):
        battle = BattleRuntime(_runtime())
        written = []
        with mock.patch.object(sys, 'stdout') as stdout:
            stdout.write = written.append
            battle._report_target_outline(0.0, 1000, None, None, None)
            battle._report_target_outline(1.0, 1001, None, None, None)
            battle._report_target_outline(5.0, 1002, None, None, None)
            for index in range(100):
                battle._report_target_outline(
                    10.0 + 5.0 * index, 2000 + index,
                    None, None, None)
                battle._report_edge('transition=%d' % index)

        targets = [line for line in written if 'TARGET ' in line]
        edges = [line for line in written if 'EDGE ' in line]
        self.assertEqual(battle._TARGET_REPORT_LIMIT, len(targets))
        self.assertNotIn('id=1001', ''.join(targets))
        self.assertEqual(battle._EDGE_REPORT_LIMIT, len(edges))

    def test_a_wreck_that_becomes_visible_again_is_restated_as_dead(self):
        """A pooled marker re-attached on re-entry is not re-stated by the
        plugin, so the startVisual tail has to push the dead state again."""
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        enemy = RemoteVehicle(
            1000, _Descriptor(), {
                'publicInfo': {'team': 2, 'name': 'Enemy'},
                'health': 0, 'isCrewActive': True, 'gunAnglesPacked': 0},
            _Vector(100.0, 0.0, 0.0), (0.0, 0.0, 0.0), runtime.math)
        enemy.model = _Model()
        enemy.appearance.attach(enemy.model)
        battle._remote_factory = types.SimpleNamespace(
            get=lambda entity_id: enemy if entity_id == 1000 else None)
        record = {
            'engine_id': 1000, 'kind': 'bot', 'network_id': 17,
            'ready': True, 'local': False, 'presentation': True,
            'visual_started': False, 'spot_visible': False,
            'state': {'team': 2, 'health': 0, 'alive': False}}
        battle._records = {'bot:17': record}
        feedback = battle._avatar.guiSessionProvider.shared.feedback

        battle._set_record_spot_visibility(record, True)

        battle._binding.start_vehicle_visual.assert_called_once_with(
            1000, True)
        feedback.setVehicleState.assert_called_once_with(
            1000, runtime.feedback_event_id.VEHICLE_DEAD, True)

    def test_a_dead_enemy_keeps_its_remaining_spot_memory(self):
        """Retail never hides a marker because the vehicle died; hiding is
        visibility-driven, so the destroyed plate has time to show."""
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._local_descriptor = _Descriptor()
        local = _Vehicle(
            10, battle._local_descriptor, _Vector(), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities[10] = local
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._set_record_spot_visibility = lambda record, visible: \
            record.update(spot_visible=bool(visible)) or bool(visible)
        record = {
            'engine_id': 12, 'network_id': 17, 'kind': 'bot',
            'ready': True, 'local': False, 'presentation': True,
            'tombstone': False, 'spot_visible': True,
            'spot_until': 14.0, 'spot_next': 100.0,
            'state': {'team': 2, 'health': 0, 'alive': False}}
        battle._records = {'bot:17': record}
        enemy = _Vehicle(12, _Descriptor(), _Vector(100.0, 0.0, 0.0),
                         (0, 0, 0), {'health': 0})
        runtime.bigworld.entities[12] = enemy

        self.assertFalse(battle._update_spotting(10.4))
        self.assertTrue(record['spot_visible'])

        # The memory decays on its own clock instead of being cut at death.
        battle._update_spotting(14.4)
        self.assertFalse(record['spot_visible'])

    def test_team_relay_visibility_does_not_claim_a_direct_spot(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._local_descriptor = _Descriptor()
        local = _Vehicle(
            10, battle._local_descriptor, _Vector(), (0, 0, 0),
            {'health': 500})
        ally = _Vehicle(
            11, _Descriptor(), _Vector(90.0, 0.0, 0.0), (0, 0, 0),
            {'health': 500})
        enemy = _Vehicle(
            12, _Descriptor(), _Vector(100.0, 0.0, 0.0), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities.update({10: local, 11: ally, 12: enemy})
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._spotting_observers = lambda: (
            ((0.0, 0.0, 0.0), local.typeDescriptor, local),
            ((90.0, 0.0, 0.0), ally.typeDescriptor, ally))
        battle._set_record_spot_visibility = lambda record, visible: \
            record.update(spot_visible=bool(visible)) or bool(visible)
        record = {
            'engine_id': 12, 'network_id': 17, 'kind': 'bot',
            'ready': True, 'local': False, 'presentation': True,
            'tombstone': False, 'spot_visible': False,
            'spot_until': 0.0, 'radio_spot_until': 0.0,
            'spot_next': 10.0,
            'state': {'team': 2, 'health': 500, 'alive': True}}
        battle._records = {'bot:17': record}
        sightings = iter((False, True))
        battle._spot_line_of_sight = (
            lambda *unused, **unused_kwargs: next(sightings))

        self.assertTrue(battle._update_spotting(10.0))
        self.assertTrue(record['spot_visible'])
        self.assertNotIn('spot_feedback_sent', record)
        self.assertEqual([], battle._avatar.battle_events)

    def test_direct_spot_feedback_failure_keeps_spotting_report_alive(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._local_descriptor = _Descriptor()
        battle._local_position = (0.0, 0.0, 0.0)
        local = _Vehicle(
            10, battle._local_descriptor, _Vector(), (0, 0, 0),
            {'health': 500})
        enemy = _Vehicle(
            12, _Descriptor(), _Vector(100.0, 0.0, 0.0), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities.update({10: local, 12: enemy})
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._spotting_observers = lambda: (
            ((0.0, 0.0, 0.0), local.typeDescriptor, local),)
        battle._spot_line_of_sight = mock.Mock(return_value=True)
        battle._set_record_spot_visibility = lambda record, visible: \
            record.update(spot_visible=bool(visible)) or bool(visible)
        battle._present_direct_spot = mock.Mock(
            side_effect=RuntimeError('battle ribbon callback failed'))
        battle._publish_spotted_targets = mock.Mock()
        record = {
            'engine_id': 12, 'network_id': 17, 'kind': 'bot',
            'ready': True, 'local': False, 'presentation': True,
            'tombstone': False, 'spot_visible': False,
            'spot_until': 0.0, 'spot_next': 10.0,
            'state': {'team': 2, 'health': 500, 'alive': True}}
        battle._records = {'bot:17': record}

        with contextlib.redirect_stdout(io.StringIO()) as log:
            self.assertTrue(battle._update_spotting(10.0))

        self.assertTrue(record['spot_visible'])
        battle._publish_spotted_targets.assert_called_once_with([record])
        self.assertEqual(
            1, log.getvalue().count(
                'optional spotting feedback disabled for this round'))

    def test_direct_spotting_observer_is_only_the_local_human(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._local_descriptor = _Descriptor()
        battle._local_position = (0.0, 0.0, 0.0)
        local = _Vehicle(
            10, battle._local_descriptor, _Vector(), (0, 0, 0),
            {'health': 500})
        ally = _Vehicle(
            11, _Descriptor(), _Vector(90.0, 0.0, 0.0), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities.update({10: local, 11: ally})
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._publish_local_vision_state = mock.Mock()
        battle._records = {
            'player:1': {
                'engine_id': 10, 'kind': 'player', 'network_id': 1,
                'ready': True, 'local': True, 'tombstone': False,
                'state': {'team': 1, 'health': 500, 'alive': True}},
            'player:2': {
                'engine_id': 11, 'kind': 'player', 'network_id': 2,
                'ready': True, 'local': False, 'tombstone': False,
                'state': {'team': 1, 'health': 500, 'alive': True}},
        }

        observers = battle._spotting_observers()

        self.assertEqual(1, len(observers))
        self.assertIs(local, observers[0][2])

    def test_visible_spot_probe_remains_presentation_only(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        reports = []
        battle.client.send_spotted_report = lambda targets: (
            reports.append(list(targets)) or True)
        battle._avatar = runtime.bigworld.avatar
        battle._local_descriptor = _Descriptor()
        battle._local_position = (0.0, 0.0, 0.0)
        local = _Vehicle(
            10, battle._local_descriptor, _Vector(), (0, 0, 0),
            {'health': 500})
        enemy = _Vehicle(
            12, _Descriptor(), _Vector(100.0, 0.0, 0.0), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities.update({10: local, 12: enemy})
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._records = {
            'player:1': {
                'engine_id': 10, 'kind': 'player', 'network_id': 1,
                'ready': True, 'local': True, 'tombstone': False,
                'state': {'team': 1, 'health': 500, 'alive': True}},
            'bot:17': {
                'engine_id': 12, 'kind': 'bot', 'network_id': 17,
                'ready': True, 'local': False, 'presentation': True,
                'tombstone': False, 'spot_visible': False,
                'spot_until': 0.0, 'spot_next': 10.0,
                'state': {'team': 2, 'health': 500, 'alive': True}},
        }
        battle._set_record_spot_visibility = lambda record, visible: \
            record.update(spot_visible=bool(visible)) or bool(visible)
        battle._spot_line_of_sight = mock.Mock(return_value=True)

        self.assertTrue(battle._update_spotting(10.0))
        self.assertEqual([], reports)

        # No probe is due at 10.1.  The last direct answer remains current, so
        # the presentation stays stable without publishing a verdict.
        self.assertFalse(battle._update_spotting(10.1))
        self.assertEqual([], reports)
        self.assertEqual(1, battle._spot_line_of_sight.call_count)

    def test_team_spot_memory_does_not_draw_beyond_the_1513_vehicle_aoi(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._local_position = (0.0, 0.0, 0.0)
        local = _Vehicle(
            10, _Descriptor(), _Vector(), (0, 0, 0), {'health': 500})
        ally = _Vehicle(
            11, _Descriptor(), _Vector(590.0, 0.0, 0.0), (0, 0, 0),
            {'health': 500})
        enemy = _Vehicle(
            12, _Descriptor(), _Vector(600.0, 0.0, 0.0), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities.update({10: local, 11: ally, 12: enemy})
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._spotting_observers = lambda: (
            ((0.0, 0.0, 0.0), local.typeDescriptor, local),
            ((590.0, 0.0, 0.0), ally.typeDescriptor, ally))
        battle._set_record_spot_visibility = lambda record, visible: \
            record.update(spot_visible=bool(visible)) or bool(visible)
        record = {
            'engine_id': 12, 'network_id': 17, 'kind': 'bot',
            'ready': True, 'local': False, 'presentation': True,
            'tombstone': False, 'spot_visible': False,
            'spot_until': 0.0, 'spot_next': 10.0,
            'state': {'team': 2, 'health': 500, 'alive': True}}
        battle._records = {'bot:17': record}
        sightings = iter((False, True))
        battle._spot_line_of_sight = (
            lambda *unused, **unused_kwargs: next(sightings))

        battle._update_spotting(10.0)

        self.assertEqual(20.0, record['spot_until'])
        self.assertFalse(record['spot_visible'])
        self.assertTrue(record['spot_marker_visible'])

        # Re-entering the local 565 m AOI uses the retained team-spot memory;
        # no second detection probe is needed.
        battle._local_position = (40.0, 0.0, 0.0)
        record['spot_next'] = 999.0
        battle._update_spotting(10.1)
        self.assertTrue(record['spot_visible'])

    def test_team_spot_beyond_aoi_keeps_minimap_but_hides_world_marker(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        battle._local_position = (0.0, 0.0, 0.0)
        enemy = RemoteVehicle(
            1000, _Descriptor(), {
                'publicInfo': {'team': 2, 'name': 'Enemy'},
                'health': 500, 'isCrewActive': True,
                'gunAnglesPacked': 0},
            _Vector(600.0, 0.0, 0.0), (0.0, 0.0, 0.0),
            types.SimpleNamespace(Vector3=_Vector, Matrix=_Matrix))
        enemy.model = _Model()
        enemy.model.visible = False
        enemy.appearance.attach(enemy.model)
        enemy.isStarted = True
        enemy.inWorld = True
        battle._remote_factory = types.SimpleNamespace(
            get=lambda entity_id: enemy if entity_id == 1000 else None)
        record = {
            'engine_id': 1000, 'kind': 'bot', 'network_id': 17,
            'ready': True, 'local': False, 'presentation': True,
            'visual_started': False, 'spot_visible': False,
            'spot_marker_visible': False,
            'state': {'team': 2, 'health': 500, 'alive': True}}

        self.assertEqual(
            (False, True),
            battle._apply_spot_presentation(record, enemy, True))
        self.assertFalse(enemy.model.visible)
        self.assertFalse(record['world_marker_started'])
        self.assertTrue(record['minimap_started'])
        battle._binding.start_vehicle_minimap.assert_called_once_with(1000)
        battle._binding.start_vehicle_visual.assert_not_called()
        battle._binding.start_vehicle_marker.assert_not_called()

        # Enter the circular AOI without replaying the minimap signal.
        enemy.position = _Vector(560.0, 0.0, 0.0)
        self.assertEqual(
            (True, True),
            battle._apply_spot_presentation(record, enemy, True))
        battle._binding.start_vehicle_marker.assert_called_once_with(1000)
        self.assertTrue(record['world_marker_started'])

        # Exact #1513 retains an existing world presentation through 570 m.
        enemy.position = _Vector(568.0, 0.0, 0.0)
        self.assertEqual(
            (True, True),
            battle._apply_spot_presentation(record, enemy, True))
        battle._binding.stop_vehicle_marker.assert_not_called()

        enemy.position = _Vector(571.0, 0.0, 0.0)
        self.assertEqual(
            (False, True),
            battle._apply_spot_presentation(record, enemy, True))
        battle._binding.stop_vehicle_marker.assert_called_once_with(1000)
        self.assertFalse(record['world_marker_started'])
        self.assertTrue(record['minimap_started'])

    def test_friendly_vehicle_beyond_aoi_keeps_only_its_minimap_entry(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        battle._local_position = (0.0, 0.0, 0.0)
        ally = RemoteVehicle(
            1000, _Descriptor(), {
                'publicInfo': {'team': 1, 'name': 'Ally'},
                'health': 500, 'isCrewActive': True,
                'gunAnglesPacked': 0},
            _Vector(600.0, 0.0, 0.0), (0.0, 0.0, 0.0), runtime.math)
        ally.model = _Model()
        ally.appearance.attach(ally.model)
        ally.isStarted = True
        ally.inWorld = True
        runtime.bigworld.entities[1000] = ally
        battle._remote_factory = types.SimpleNamespace(
            get=lambda entity_id: ally if entity_id == 1000 else None)
        battle._spotting_observers = lambda: ()
        record = {
            'engine_id': 1000, 'kind': 'bot', 'network_id': 17,
            'ready': True, 'local': False, 'presentation': True,
            'tombstone': False, 'native_remote': False,
            'world_marker_started': True, 'minimap_started': True,
            'spot_visible': True, 'spot_marker_visible': True,
            'state': {'team': 1, 'health': 500, 'alive': True}}
        battle._records = {'bot:17': record}

        self.assertTrue(battle._update_spotting(10.0))
        self.assertFalse(record['spot_visible'])
        self.assertTrue(record['spot_marker_visible'])
        self.assertFalse(record['world_marker_started'])
        self.assertTrue(record['minimap_started'])
        battle._binding.stop_vehicle_marker.assert_called_once_with(1000)
        battle._binding.stop_vehicle_minimap.assert_not_called()

        ally.position = _Vector(560.0, 0.0, 0.0)
        self.assertTrue(battle._update_spotting(10.1))
        self.assertTrue(record['spot_visible'])
        self.assertTrue(record['world_marker_started'])
        battle._binding.start_vehicle_marker.assert_called_once_with(1000)
        battle._binding.start_vehicle_minimap.assert_not_called()

    def test_strategic_spg_view_draws_team_spotted_target_beyond_aoi(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._local_descriptor = _Descriptor()
        battle._local_descriptor.type.tags = ('SPG',)
        battle._local_position = (0.0, 0.0, 0.0)
        local = _Vehicle(
            10, battle._local_descriptor, _Vector(), (0, 0, 0),
            {'health': 500})
        enemy = _Vehicle(
            12, _Descriptor(), _Vector(700.0, 0.0, 0.0), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities.update({10: local, 12: enemy})
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._set_record_spot_visibility = lambda record, visible: \
            record.update(spot_visible=bool(visible)) or bool(visible)
        record = {
            'engine_id': 12, 'network_id': 17, 'kind': 'bot',
            'ready': True, 'local': False, 'presentation': True,
            'tombstone': False, 'spot_visible': False,
            'spot_marker_visible': True,
            'spot_until': 20.0, 'spot_next': 999.0,
            'state': {'team': 2, 'health': 500, 'alive': True}}
        battle._records = {'bot:17': record}

        battle._avatar.inputHandler._AvatarInputHandler__ctrlModeName = \
            'strategic'
        self.assertTrue(battle._update_spotting(10.0))
        self.assertTrue(record['spot_visible'])
        self.assertTrue(record['spot_marker_visible'])

        battle._avatar.inputHandler._AvatarInputHandler__ctrlModeName = \
            'arcade'
        self.assertTrue(battle._update_spotting(10.1))
        self.assertFalse(record['spot_visible'])
        self.assertTrue(record['spot_marker_visible'])

    def test_dead_local_vehicle_uses_server_validated_ally_relay(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._local_descriptor = _Descriptor()
        battle._local_position = (0.0, 0.0, 0.0)
        local = _Vehicle(
            10, battle._local_descriptor, _Vector(), (0, 0, 0),
            {'health': 500})
        ally = _Vehicle(
            11, _Descriptor(), _Vector(90.0, 0.0, 0.0), (0, 0, 0),
            {'health': 500})
        enemy = _Vehicle(
            12, _Descriptor(), _Vector(100.0, 0.0, 0.0), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities.update({10: local, 11: ally, 12: enemy})
        battle._server = types.SimpleNamespace(vehicle_id=10)
        target = {
            'engine_id': 12, 'network_id': 17, 'kind': 'bot',
            'ready': True, 'local': False, 'presentation': True,
            'tombstone': False, 'spot_visible': False,
            'spot_until': 0.0, 'spot_next': 10.0,
            'state': {'team': 2, 'health': 500, 'alive': True}}
        battle._records = {
            'player:1': {
                'engine_id': 10, 'kind': 'player', 'network_id': 1,
                'ready': True, 'local': True, 'tombstone': False,
                'state': {'team': 1, 'health': 0, 'alive': False,
                          'display_health': 500}},
            'player:2': {
                'engine_id': 11, 'kind': 'player', 'network_id': 2,
                'ready': True, 'local': False, 'tombstone': False,
                'state': {'team': 1, 'health': 500, 'alive': True}},
            'bot:17': target,
        }
        battle._set_record_spot_visibility = lambda record, visible: \
            record.update(spot_visible=bool(visible)) or bool(visible)
        battle._spot_line_of_sight = mock.Mock(return_value=True)

        self.assertTrue(battle._apply_team_observation({
            'type': 'bot_observation',
            'contacts': [{
                'observing_team': 1, 'target_team': 2,
                'target_kind': 'bot', 'target_id': 17,
                'visible': True, 'fresh': True, 'time_left': 10.0,
                'visible_by_bot_ids': [11],
                'visible_by_player_ids': [],
                'shootable_by_bot_ids': [],
            }],
        }, 10.0))

        self.assertTrue(target['spot_visible'])
        self.assertEqual(0.0, target['spot_until'])
        self.assertEqual(20.0, target['radio_spot_until'])
        battle._spot_line_of_sight.assert_not_called()
        self.assertNotIn('spot_feedback_sent', target)
        self.assertEqual([], battle._avatar.battle_events)

    def test_dead_local_spot_memory_expires_without_renewal_or_feedback(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        battle._local_descriptor = _Descriptor()
        battle._local_position = (0.0, 0.0, 0.0)
        local = _Vehicle(
            10, battle._local_descriptor, _Vector(), (0, 0, 0),
            {'health': 500})
        enemy = _Vehicle(
            12, _Descriptor(), _Vector(100.0, 0.0, 0.0), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities.update({10: local, 12: enemy})
        battle._server = types.SimpleNamespace(vehicle_id=10)
        target = {
            'engine_id': 12, 'network_id': 17, 'kind': 'bot',
            'ready': True, 'local': False, 'presentation': True,
            'tombstone': False, 'spot_visible': True,
            'spot_until': 10.0, 'spot_next': 9.9,
            'state': {'team': 2, 'health': 500, 'alive': True}}
        battle._records = {
            'player:1': {
                'engine_id': 10, 'kind': 'player', 'network_id': 1,
                'ready': True, 'local': True, 'tombstone': False,
                'state': {'team': 1, 'health': 0, 'alive': False,
                          'display_health': 500}},
            'bot:17': target,
        }
        battle._set_record_spot_visibility = lambda record, visible: \
            record.update(spot_visible=bool(visible)) or bool(visible)
        battle._spot_line_of_sight = mock.Mock(return_value=True)

        self.assertFalse(battle._update_spotting(9.9))
        self.assertTrue(target['spot_visible'])
        self.assertTrue(battle._update_spotting(10.0))
        self.assertFalse(target['spot_visible'])
        self.assertFalse(battle._update_spotting(10.5))

        self.assertEqual(10.0, target['spot_until'])
        battle._spot_line_of_sight.assert_not_called()
        self.assertNotIn('spot_feedback_sent', target)
        self.assertEqual([], battle._avatar.battle_events)

    def test_enemy_spotting_staggers_worst_case_native_los_rays(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.client = _Client()
        battle._avatar = runtime.bigworld.avatar
        observer_descriptor = _Descriptor()
        battle._spotting_observers = lambda: (
            ((0.0, 0.0, 0.0), observer_descriptor, None),)

        def set_visibility(record, visible):
            record['spot_visible'] = bool(visible)
            return bool(visible)

        battle._set_record_spot_visibility = set_visibility
        rays = []

        def blocked_ray(*args):
            rays.append(args)
            return (_Vector(),)

        runtime.bigworld.wg_collideSegment = blocked_ray
        for offset, network_id in enumerate(range(11, 26)):
            engine_id = 1000 + offset
            runtime.bigworld.entities[engine_id] = _Vehicle(
                engine_id, _Descriptor(), _Vector(100.0, 0.0, 0.0),
                (0.0, 0.0, 0.0), {'health': 500})
            battle._records['bot:%d' % network_id] = {
                'engine_id': engine_id, 'kind': 'bot',
                'network_id': network_id, 'ready': True,
                'local': False, 'presentation': True, 'tombstone': False,
                'spot_visible': False, 'spot_until': 0.0,
                'spot_next': 0.0,
                'state': {'team': 2, 'health': 500, 'alive': True}}

        per_update = []
        for now in (10.0, 10.1, 10.2, 10.3, 10.4,
                    10.5, 10.6, 10.7, 10.8, 10.9):
            before = len(rays)
            battle._update_spotting(now)
            per_update.append(len(rays) - before)

        # Each client proves only its local human's direct sight. Bot and
        # remote-human sightings arrive through the server-merged team relay,
        # so three phased enemies cost two static rays apiece instead of the
        # old 3 enemies x 15 duplicated observers x 2 rays.
        self.assertEqual([6] * 10, per_update)

    def test_spotting_uses_descriptor_camouflage_and_shot_factor(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        observer = _Descriptor()
        target = _Descriptor()
        crew_factors = []

        def base_invisibility(crew_factor, camouflage_id):
            crew_factors.append((crew_factor, camouflage_id))
            return (0.50, 0.50)

        target.computeBaseInvisibility = base_invisibility
        target.gun.invisibilityFactorAtShot = 0.25
        sight = ((0.0, 0.0, 0.0), observer, None)
        target_position = (285.0, 0.0, 0.0)

        self.assertFalse(battle._spot_line_of_sight(
            sight, target_position, target, False, False))
        self.assertTrue(battle._spot_line_of_sight(
            sight, target_position, target, False, True))
        # VehicleDescrCrew._processSkills: 0.57 with no camouflage skill.
        self.assertAlmostEqual(0.57, crew_factors[0][0])
        self.assertIsNone(crew_factors[0][1])

    def test_damaged_optics_and_crew_reduce_observer_view_range(self):
        battle = BattleRuntime(_runtime())
        descriptor = _Descriptor()
        observer = _Vehicle(
            10, descriptor, _Vector(), (0, 0, 0), {'health': 500})

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'critical_damage.stat_factor', return_value=0.5):
            damaged = battle._vision_radius(descriptor, observer)

        healthy = battle._vision_radius(descriptor)
        self.assertAlmostEqual(healthy * 0.5, damaged)

    def test_spotting_applies_pair_foliage_and_near_bush_shot_rule(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        observer = _Descriptor()
        target = _Descriptor()
        target.computeBaseInvisibility = lambda *unused: (0.0, 0.0)
        calls = []

        def foliage_bonus(unused_observer, unused_target, fired_recently):
            calls.append(fired_recently)
            return 0.0 if fired_recently else 0.60

        battle._foliage = types.SimpleNamespace(
            camouflage_bonus=foliage_bonus)
        sight = ((0.0, 0.0, 0.0), observer, None)
        target_position = (250.0, 0.0, 0.0)

        self.assertFalse(battle._spot_line_of_sight(
            sight, target_position, target, False, False))
        self.assertTrue(battle._spot_line_of_sight(
            sight, target_position, target, False, True))
        self.assertEqual([False, True], calls)

    def test_runtime_foliage_failure_falls_back_to_zero_bonus_once(self):
        battle = BattleRuntime(_runtime())
        camouflage_bonus = mock.Mock(
            side_effect=RuntimeError('foliage query failed'))
        battle._foliage = types.SimpleNamespace(
            camouflage_bonus=camouflage_bonus)

        with contextlib.redirect_stdout(io.StringIO()) as log:
            self.assertEqual(0.0, battle._foliage_camouflage_bonus(
                (0.0, 0.0, 0.0), (10.0, 0.0, 0.0), False))
            self.assertEqual(0.0, battle._foliage_camouflage_bonus(
                (0.0, 0.0, 0.0), (10.0, 0.0, 0.0), False))

        camouflage_bonus.assert_called_once_with(
            (0.0, 0.0, 0.0), (10.0, 0.0, 0.0), False)
        self.assertIsNone(battle._foliage)
        self.assertEqual(
            1, log.getvalue().count(
                'optional foliage camouflage disabled for this round'))

    def test_dead_local_vehicle_cannot_move_or_fire(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()
        battle.client = client
        battle.state = 'running'
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(0, 0, 0), (0, 0, 0),
                          {'health': 0})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=1.0, turn=1.0,
            send_current=lambda: client.send_input('current'))
        battle._local_speed = 5.0

        battle._drive_local(0.1)

        self.assertEqual(0.0, battle._local_speed)
        self.assertEqual(0.0, battle._sender.forward)
        self.assertEqual(0.0, battle._sender.turn)
        self.assertFalse(battle.shoot(0.0, 0.0))
        self.assertFalse(any(kind == 'fire' for kind, unused in client.sent))

    def test_authoritative_shot_enters_native_1513_bloom_once(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()
        descriptor = _Descriptor()
        entity = _Vehicle(
            10, descriptor, _Vector(0, 0, 0), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle.client = client
        battle.state = 'running'
        battle._battle_live = True
        battle._avatar = runtime.bigworld.avatar
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = _LANInputSender(battle)
        battle._gun_state = gun_mechanics.GunState(descriptor)
        battle._gun_state.reload_time = 0.0
        battle._gun_state.clip = 1
        battle._publish_ammo_state = mock.Mock()
        battle._publish_reload_event = mock.Mock()
        battle._resolve_hit = mock.Mock()
        battle._local_yaw = 0.4
        battle._avatar.gunRotator.turretYaw = 0.15
        battle._avatar.gunRotator.gunPitch = -0.08
        battle._sender.aim_pitch = -0.25
        battle._records = {
            'player:1': {'engine_id': 10, 'local': True}}

        def stock_show_shooting(burst, is_predicted=False):
            entity.last_shot = (burst, is_predicted)
            battle._avatar.getOwnVehicleShotDispersionAngle(
                battle._avatar.gunRotator.turretRotationSpeed, 1)

        entity.showShooting = stock_show_shooting

        self.assertTrue(battle.shoot(0.2, -0.1))
        self.assertEqual([], battle._avatar.dispersion_queries)
        self.assertEqual(1, battle._gun_state.clip)
        intent = next(item for item in client.sent
                      if item[0] == 'fire_intent')
        self.assertEqual((0,), intent[1])
        self.assertEqual({
            'input_seq': 1, 'intent_seq': 1,
            'shot_origin': [0.0, 2.0, 0.0],
            'shot_direction': [0.0, 0.0, 1.0],
            'dispersion_angle': 0.25,
        }, intent[2])
        current_input = next(item for item in client.sent
                             if item[0] == 'input')
        self.assertAlmostEqual(0.55, current_input[1][2])
        self.assertAlmostEqual(-0.08, current_input[1][3])
        self.assertAlmostEqual(0.55, battle._sender.aim_yaw)
        self.assertAlmostEqual(-0.08, battle._sender.gun_pitch)
        self.assertAlmostEqual(-0.25, battle._sender.aim_pitch)
        battle._show_shot({
            'attacker': 1, 'shooter_kind': 'player', 'shooter_id': 1,
            'fire_intent_seq': 1, 'fire_input_seq': 1,
            'shot_seq': 1, 'shell_index': 0,
        })

        self.assertEqual([(0.5, 1)], battle._avatar.dispersion_queries)
        self.assertEqual((1, False), entity.last_shot)
        battle._resolve_hit.assert_not_called()
        self.assertFalse(any(item[0] == 'fire' for item in client.sent))

    def test_authoritative_shot_seeds_stateful_native_convergence(self):
        class StockLikeDispersion(object):
            def __init__(self):
                self.factor = 1.0
                self.calls = []

            def __call__(self, turret_speed, with_shot=0):
                self.calls.append((turret_speed, with_shot))
                if with_shot == 1:
                    self.factor = math.sqrt(self.factor ** 2 + 4.0 ** 2)
                else:
                    self.factor = 1.0 + (self.factor - 1.0) * 0.75
                return [0.1 * self.factor,
                        0.1 * (math.sqrt(17.0) if with_shot else 1.0)]

        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()
        descriptor = _Descriptor()
        entity = _Vehicle(
            10, descriptor, _Vector(0, 0, 0), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities[10] = entity
        producer = StockLikeDispersion()
        runtime.bigworld.avatar.getOwnVehicleShotDispersionAngle = producer
        battle.client = client
        battle.state = 'running'
        battle._battle_live = True
        battle._avatar = runtime.bigworld.avatar
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = _LANInputSender(battle)
        battle._gun_state = gun_mechanics.GunState(descriptor)
        battle._gun_state.reload_time = 0.0
        battle._gun_state.clip = 1
        battle._publish_ammo_state = mock.Mock()
        battle._publish_reload_event = mock.Mock()
        battle._resolve_hit = mock.Mock()
        battle._records = {
            'player:1': {'engine_id': 10, 'local': True}}

        def stock_show_shooting(burst, is_predicted=False):
            entity.last_shot = (burst, is_predicted)
            producer(battle._avatar.gunRotator.turretRotationSpeed, 1)

        entity.showShooting = stock_show_shooting

        self.assertTrue(battle.shoot(0.2, -0.1))
        self.assertEqual([], producer.calls)
        intent = [message for message in client.sent
                  if message[0] == 'fire_intent'][-1]
        self.assertEqual((0,), intent[1])
        self.assertNotIn('source_shot', intent[2])
        self.assertNotIn('velocity', intent[2])
        battle._show_shot({
            'attacker': 1, 'shooter_kind': 'player', 'shooter_id': 1,
            'fire_intent_seq': 1, 'fire_input_seq': 1,
            'shot_seq': 1, 'shell_index': 0,
        })
        shot_angle = producer.factor
        first_tick = producer(0.5, 0)[0]
        second_tick = producer(0.5, 0)[0]

        self.assertEqual((0.5, 1), producer.calls[0])
        self.assertGreater(shot_angle, 1.0)
        self.assertGreater(0.1 * shot_angle, first_tick)
        self.assertGreater(first_tick, second_tick)
        self.assertGreater(second_tick, 0.1)

    def test_rejected_shot_does_not_enter_native_1513_bloom(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()
        client.send_fire_intent = mock.Mock(return_value=0)
        descriptor = _Descriptor()
        entity = _Vehicle(
            10, descriptor, _Vector(0, 0, 0), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle.client = client
        battle.state = 'running'
        battle._battle_live = True
        battle._avatar = runtime.bigworld.avatar
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = _LANInputSender(battle)
        battle._gun_state = gun_mechanics.GunState(descriptor)
        battle._gun_state.reload_time = 0.0
        battle._gun_state.clip = 1
        battle._avatar.gunRotator.turretYaw = 0.2
        battle._avatar.gunRotator.gunPitch = -0.1

        self.assertFalse(battle.shoot(0.2, -0.1))
        self.assertEqual([], battle._avatar.dispersion_queries)
        client.send_fire_intent.assert_called_once_with(
            0, [0.0, 2.0, 0.0], [0.0, 0.0, 1.0], 0.25)

    def test_trigger_advances_gun_through_hud_ready_edge_before_validation(self):
        runtime = _runtime()
        runtime.bigworld.now = 10.0
        battle = BattleRuntime(runtime)
        client = _Client()
        descriptor = _Descriptor()
        entity = _Vehicle(
            10, descriptor, _Vector(0, 0, 0), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle.client = client
        battle.state = 'running'
        battle._battle_live = True
        battle._avatar = runtime.bigworld.avatar
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = _LANInputSender(battle)
        battle._gun_state = gun_mechanics.GunState(descriptor)
        battle._gun_state.reload_time = 0.05
        battle._gun_state.clip = 0
        battle._gun_last_tick = 9.94

        self.assertTrue(battle.shoot(0.0, 0.0))

        self.assertEqual(0.0, battle._gun_state.reload_time)
        self.assertEqual(1, battle._gun_state.clip)
        self.assertEqual(1, battle._local_fire_intent['intent_seq'])

    def test_delayed_input_consumes_full_reload_gap_in_same_checkpoint(self):
        runtime = _runtime()
        runtime.bigworld.now = 10.0
        battle = BattleRuntime(runtime)
        client = _Client()
        descriptor = _Descriptor()
        entity = _Vehicle(
            10, descriptor, _Vector(0, 0, 0), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle.client = client
        battle.state = 'running'
        battle._battle_live = True
        battle._avatar = runtime.bigworld.avatar
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = _LANInputSender(battle)
        battle._gun_state = gun_mechanics.GunState(descriptor)
        battle._gun_state.reload_time = 1.25
        battle._gun_state.reload_duration = 1.5
        battle._gun_state.clip = 0
        battle._gun_last_tick = 8.75

        self.assertTrue(battle._sender.send_current())

        self.assertEqual(10.0, battle._gun_last_tick)
        self.assertEqual(0.0, battle._gun_state.reload_time)
        self.assertEqual(1, battle._gun_state.clip)
        checkpoint = client.sent[-1][2]['gun_checkpoint']
        self.assertEqual(0.0, checkpoint['reload_time'])
        self.assertEqual(1, checkpoint['clip'])

    def test_fire_intent_result_releases_visible_pending_trigger(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._start_message = {'round_id': 7}
        battle._gun_state = types.SimpleNamespace(
            ammo=[20, 10], clip=2, reload_time=1.25,
            reload_duration=5.0, shot_index=0, pending_index=None)
        battle._local_fire_intent = {
            'intent_seq': 3, 'input_seq': 4, 'sent_at': 1.0}
        gun_before = (
            list(battle._gun_state.ammo), battle._gun_state.clip,
            battle._gun_state.reload_time,
            battle._gun_state.reload_duration,
            battle._gun_state.shot_index,
            battle._gun_state.pending_index)

        self.assertTrue(battle.on_fire_intent_result({
            'type': 'fire_intent_result', 'round_id': 7,
            'player_id': 1, 'intent_seq': 3, 'accepted': False,
            'reason': 'projectile_launch_rejected',
        }))

        self.assertIsNone(battle._local_fire_intent)
        battle._avatar.cancelWaitingForShot.assert_called_once_with()
        self.assertEqual(gun_before, (
            list(battle._gun_state.ammo), battle._gun_state.clip,
            battle._gun_state.reload_time,
            battle._gun_state.reload_duration,
            battle._gun_state.shot_index,
            battle._gun_state.pending_index))

    def test_worker_fire_intent_ignores_transport_receipt_time(self):
        battle = BattleRuntime(_runtime())
        battle._worker_mode = True
        battle.state = 'running'
        battle.client = _Client()
        battle.client.authority_epoch = 4
        battle._start_message = {'round_id': 7}
        intent = {
            'type': 'fire_intent', 'round_id': 7,
            'authority_epoch': 4, 'player_id': 2, 'intent_seq': 3,
            'shot_seq': 5, 'input_seq': 8, 'pose_time_us': 1000,
            'shell_index': 0, 'next_shell_index': 0,
            'shell_change_pending': False,
            'gun_checkpoint_seq': 8,
            'gun_checkpoint': _human_gun_checkpoint(),
            'aim_yaw': 0.2, 'gun_pitch': -0.1,
            'x': 1.0, 'y': 2.0, 'z': 3.0, 'yaw': 0.0,
            'pitch': 0.0, 'roll': 0.0, 'speed': 4.0,
            'shot_origin': [1.0, 3.0, 3.0],
            'shot_direction': [0.0, 0.0, 1.0],
            'dispersion_angle': 0.02,
            '_client_received_time': 10.0,
            '_client_dispatch_delay': 0.01,
        }

        self.assertTrue(battle.on_fire_intent(intent))
        retry = dict(
            intent, _client_received_time=10.5,
            _client_dispatch_delay=0.02)
        self.assertTrue(battle.on_fire_intent(retry))

        stored = battle._player_fire_intents[(2, 3)]
        self.assertNotIn('_client_received_time', stored)
        self.assertNotIn('_client_dispatch_delay', stored)
        self.assertEqual(1, len(battle._player_fire_intents))
        with self.assertRaisesRegex(
                RuntimeError, 'worker fire intent is malformed'):
            battle.on_fire_intent(dict(intent, unexpected='wire field'))

    def test_worker_resolves_immediate_player_destructible_contact(self):
        battle = BattleRuntime(_runtime())
        battle._worker_mode = True
        battle.state = 'running'
        battle._start_message = {'round_id': 7}
        battle.client = _Client()
        battle.client.authority_epoch = 4
        battle._clock = mock.Mock(return_value=12.5)
        battle._resolve_player_destructible_contacts = mock.Mock(
            return_value=1)
        player = {
            'id': 2, 'vehicle': 'ussr:R11_MS-1',
            'vehicle_compact_descr': 'dGVzdA==',
            'effective_params': _effective_params_snapshot(),
            'destructible_contacts': [{
                'seq': 3, 'x': 1.0, 'y': 2.0, 'z': 3.0,
                'yaw': 0.25, 'speed': 8.0, 'dt': 0.04,
                'forward': 1.0, 'token': [[22, 37, None]],
            }],
        }
        message = {
            'type': 'player_destructible_contact',
            'protocol': 5, 'round_id': 7, 'authority_epoch': 4,
            'player': player,
            '_client_dispatch_delay': 0.01,
        }

        self.assertTrue(battle.on_player_destructible_contact(message))
        battle._resolve_player_destructible_contacts.assert_called_once_with(
            [player], 12.5)
        with self.assertRaisesRegex(
                RuntimeError,
                'worker player destructible contact body is malformed'):
            battle.on_player_destructible_contact(dict(
                message, player=dict(player, unexpected='wire field')))

    def test_worker_accepts_visible_destructible_token_inside_cluster(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._worker_mode = True
        battle._avatar = runtime.bigworld.avatar
        battle.client = _Client()
        battle.client.send_player_destructible_contact_result = mock.Mock(
            return_value=True)
        requested = [[22, 37, None]]
        worker_token = ((22, 37, None), (22, 38, None))
        battle._destructibles = types.SimpleNamespace(
            _catalog_motion_proposal=mock.Mock(return_value={
                'status': 'crushed', 'token': worker_token,
                'requires_commit': True,
            }),
            _catalog_motion_blocked=mock.Mock(return_value={
                'status': 'crushed', 'token': worker_token,
            }))
        battle._resolve_player_descriptor = mock.Mock(
            return_value=_Descriptor())
        player = {
            'id': 2, 'vehicle': 'ussr:R11_MS-1',
            'vehicle_compact_descr': 'dGVzdA==',
            'effective_params': _effective_params_snapshot(),
            'destructible_contacts': [{
                'seq': 3, 'x': 1.0, 'y': 2.0, 'z': 3.0,
                'yaw': 0.25, 'speed': 8.0, 'dt': 0.04,
                'forward': 1.0, 'token': requested,
            }],
        }

        authority_name = (
            'gui.mods.offline_lan_0922.destructibles_authority')
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused_key: False)
        package = sys.modules['gui.mods.offline_lan_0922']
        with mock.patch.dict(sys.modules, {authority_name: authority}), \
                mock.patch.object(
                    package, 'destructibles_authority', authority,
                    create=True):
            self.assertEqual(
                1, battle._resolve_player_destructible_contacts(
                    [player], 12.5))

        battle._destructibles._catalog_motion_blocked.assert_called_once()
        battle.client.send_player_destructible_contact_result.\
            assert_called_once_with(2, 3, True, requested)

    def test_worker_accepts_idempotent_destructible_contact(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._worker_mode = True
        battle._avatar = runtime.bigworld.avatar
        battle.client = _Client()
        battle.client.send_player_destructible_contact_result = mock.Mock(
            return_value=True)
        requested = [[22, 37, None]]
        battle._destructibles = types.SimpleNamespace(
            _catalog_motion_proposal=mock.Mock(return_value={
                'status': 'clear', 'token': None,
                'requires_commit': False,
            }),
            _catalog_motion_blocked=mock.Mock())
        battle._resolve_player_descriptor = mock.Mock(
            return_value=_Descriptor())
        player = {
            'id': 2, 'vehicle': 'ussr:R11_MS-1',
            'vehicle_compact_descr': 'dGVzdA==',
            'effective_params': _effective_params_snapshot(),
            'destructible_contacts': [{
                'seq': 3, 'x': 1.0, 'y': 2.0, 'z': 3.0,
                'yaw': 0.25, 'speed': 8.0, 'dt': 0.04,
                'forward': 1.0, 'token': requested,
            }],
        }

        authority_name = (
            'gui.mods.offline_lan_0922.destructibles_authority')
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused_key: True)
        package = sys.modules['gui.mods.offline_lan_0922']
        with mock.patch.dict(sys.modules, {authority_name: authority}), \
                mock.patch.object(
                    package, 'destructibles_authority', authority,
                    create=True):
            self.assertEqual(
                1, battle._resolve_player_destructible_contacts(
                    [player], 12.5))

        battle._destructibles._catalog_motion_blocked.assert_not_called()
        battle.client.send_player_destructible_contact_result.\
            assert_called_once_with(2, 3, True, requested)

    def test_worker_rejects_crush_proposal_with_a_backing_wall(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._worker_mode = True
        battle._avatar = runtime.bigworld.avatar
        battle.client = _Client()
        battle.client.send_player_destructible_contact_result = mock.Mock(
            return_value=True)
        requested = [[22, 37, None]]
        worker_token = ((22, 37, None),)
        battle._destructibles = types.SimpleNamespace(
            _catalog_motion_proposal=mock.Mock(return_value={
                'status': 'crushed', 'token': worker_token,
                'requires_commit': True,
            }),
            _catalog_motion_blocked=mock.Mock())
        battle._resolve_player_descriptor = mock.Mock(
            return_value=_Descriptor())
        player = {
            'id': 2, 'vehicle': 'ussr:R11_MS-1',
            'vehicle_compact_descr': 'dGVzdA==',
            'effective_params': _effective_params_snapshot(),
            'destructible_contacts': [{
                'seq': 3, 'x': 1.0, 'y': 2.0, 'z': 3.0,
                'yaw': 0.25, 'speed': 8.0, 'dt': 0.04,
                'forward': 1.0, 'token': requested,
            }],
        }

        authority_name = (
            'gui.mods.offline_lan_0922.destructibles_authority')
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused_key: False)
        package = sys.modules['gui.mods.offline_lan_0922']
        with mock.patch.dict(sys.modules, {authority_name: authority}), \
                mock.patch.object(
                    package, 'destructibles_authority', authority,
                    create=True), \
                mock.patch(
                    'gui.mods.offline_lan_0922.battle_runtime.'
                    'world_collision.check_horizontal_collision',
                    return_value='hard'):
            self.assertEqual(
                1, battle._resolve_player_destructible_contacts(
                    [player], 12.5))

        battle._destructibles._catalog_motion_blocked.assert_not_called()
        battle.client.send_player_destructible_contact_result.\
            assert_called_once_with(2, 3, False, requested)

    def test_worker_hard_destructible_block_never_rewinds_visible_pose(self):
        runtime = _runtime()
        worker = BattleRuntime(runtime)
        worker._worker_mode = True
        worker._avatar = runtime.bigworld.avatar
        worker.client = _Client()
        worker.client.send_player_destructible_contact_result = mock.Mock(
            return_value=True)
        worker._destructibles = types.SimpleNamespace(
            _catalog_motion_proposal=mock.Mock(return_value={
                'status': 'hard',
                'token': ((22, 37, None), (22, 38, None)),
                'requires_commit': False,
            }),
            _catalog_motion_blocked=mock.Mock())
        worker._resolve_player_descriptor = mock.Mock(
            return_value=_Descriptor())
        requested = [[22, 37, None]]
        player = {
            'id': 2, 'vehicle': 'ussr:R11_MS-1',
            'vehicle_compact_descr': 'dGVzdA==',
            'effective_params': _effective_params_snapshot(),
            'destructible_contacts': [{
                'seq': 3, 'x': 1.0, 'y': 2.0, 'z': 3.0,
                'yaw': 0.25, 'speed': 8.0, 'dt': 0.04,
                'forward': 1.0, 'token': requested,
            }],
        }

        authority_name = (
            'gui.mods.offline_lan_0922.destructibles_authority')
        authority = types.SimpleNamespace(
            is_destroyed=lambda *unused_key: False)
        package = sys.modules['gui.mods.offline_lan_0922']
        with mock.patch.dict(sys.modules, {authority_name: authority}), \
                mock.patch.object(
                    package, 'destructibles_authority', authority,
                    create=True):
            self.assertEqual(
                1, worker._resolve_player_destructible_contacts(
                    [player], 12.5))

        worker._destructibles._catalog_motion_blocked.assert_not_called()
        worker.client.send_player_destructible_contact_result.\
            assert_called_once_with(2, 3, False, requested)

        visible = BattleRuntime(_runtime())
        visible.client = _Client()
        visible._start_message = {'round_id': 7}
        self.assertTrue(visible._queue_local_destructible_contact(
            {'requires_commit': True, 'token': requested},
            (1.0, 2.0, 3.0), 0.25, 8.0, 0.04))
        visible._local_position = (1.0, 2.0, 3.5)
        visible._local_speed = 8.0
        self.assertTrue(visible.on_player_destructible_contact_result({
            'type': 'player_destructible_contact_result',
            'round_id': 7, 'contact_seq': 1, 'accepted': False,
            'x': 1.0, 'y': 2.0, 'z': 3.0, 'yaw': 0.25,
        }))
        self.assertEqual((1.0, 2.0, 3.5), visible._local_position)
        self.assertEqual(8.0, visible._local_speed)

    def test_splash_wire_keeps_impact_and_target_pose_distinct(self):
        battle = BattleRuntime(_runtime())

        effect = battle._projectile_effect(
            {'kind': 'bot', 'network_id': 2}, 40, 2,
            (10.0, 1.0, 0.0), None, None, None,
            (20.0, 2.0, 0.0))

        self.assertEqual((10.0, 1.0, 0.0), (
            effect['x'], effect['y'], effect['z']))
        self.assertEqual((20.0, 2.0, 0.0), (
            effect['target_x'], effect['target_y'], effect['target_z']))
        self.assertNotIn('damage_sticker', effect)

        direct = battle._projectile_effect(
            {'kind': 'bot', 'network_id': 2}, 40, 2,
            (10.0, 1.0, 0.0), None, None, None,
            damage_sticker=12345678901234567890)
        self.assertEqual(
            12345678901234567890, direct['damage_sticker'])

    def test_worker_launch_uses_visible_trigger_ray_not_model_node(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._worker_mode = True
        battle.state = 'running'
        battle._battle_live = True
        battle._start_message = {'round_id': 7}
        battle._projectile_is_authority = lambda: True
        battle._config = {'perfect_accuracy': True}
        client = _Client()
        client.authority_epoch = 4
        client.send_projectile_launch = mock.Mock(
            side_effect=lambda *args, **unused_kwargs: args[2])
        battle.client = client
        entity = _Vehicle(
            11, _Descriptor(), _Vector(50.0, 0.0, 50.0), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities[11] = entity
        effective = _effective_params_snapshot(ammo=[[101, 40]])
        donated_shot = effective['gun']['shots'][0]['source_shot']
        donated_shot['speed'] = 625.0
        donated_shot['gravity'] = 17.0
        donated_shot['maxDistance'] = 777.0
        donated_shot['piercingPower'] = [321.0, 222.0]
        donated_shot['shell']['damage'] = [390.0, 165.0]
        battle._records = {'player:2': {
            'engine_id': 11, 'network_id': 2, 'kind': 'player',
            'local': False, 'ready': True, 'tombstone': False,
            'state': {
                'alive': True, 'shell_index': 0, 'speed': 0.0,
                'turn': 0.0, 'effective_params': effective,
            },
        }}
        gun = gun_mechanics.GunState(
            entity.typeDescriptor, effective['loadout'])
        gun.bind_client_contract(effective['gun'], {101: 40})
        # The worker's independently advanced copy is deliberately stale.
        # The input-bound visible checkpoint below is the final fire edge.
        gun.reload_time = 4.0
        gun.clip = 0
        gun._effective_params = effective
        battle._player_authority_guns = {2: gun}
        intent = {
            'type': 'fire_intent', 'round_id': 7,
            'authority_epoch': 4, 'player_id': 2, 'intent_seq': 3,
            'shot_seq': 5, 'input_seq': 8, 'pose_time_us': 1000,
            'shell_index': 0, 'next_shell_index': 0,
            'shell_change_pending': False,
            'gun_checkpoint_seq': 8,
            'gun_checkpoint': _human_gun_checkpoint(),
            'aim_yaw': 0.2, 'gun_pitch': -0.1,
            'x': 50.0, 'y': 0.0, 'z': 50.0, 'yaw': 0.0,
            'pitch': 0.0, 'roll': 0.0, 'speed': 0.0,
            'shot_origin': [4.0, 2.0, 8.0],
            'shot_direction': [0.6, 0.0, 0.8],
            'dispersion_angle': 0.02,
        }

        with mock.patch.object(
                type(entity.model), 'node',
                side_effect=AssertionError(
                    'stale model node must not be read')) as node:
            self.assertTrue(battle.on_fire_intent(intent))
            self.assertTrue(
                battle._advance_player_fire_authority(0.1, 10.0))
            node.assert_not_called()

        call = client.send_projectile_launch.call_args
        self.assertEqual(['player', 2, 5, 0], list(call.args[:4]))
        self.assertEqual([4.0, 2.0, 8.0], call.args[4])
        self.assertEqual([375.0, 0.0, 500.0], call.args[5])
        self.assertEqual((17.0, 777.0), call.args[6:8])
        self.assertEqual([321.0, 222.0],
                         call.kwargs['source_shot']['piercingPower'])
        self.assertEqual([390.0, 165.0],
                         call.kwargs['source_shot']['shell']['damage'])

    def test_worker_promotes_the_queued_shell_at_the_shot_boundary(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._worker_mode = True
        battle.state = 'running'
        battle._battle_live = True
        battle._start_message = {'round_id': 7}
        battle._projectile_is_authority = lambda: True
        battle._config = {'perfect_accuracy': True}
        client = _Client()
        client.authority_epoch = 4
        client.send_projectile_launch = mock.Mock(
            side_effect=lambda *args, **unused_kwargs: args[2])
        battle.client = client
        descriptor = _Descriptor()
        second_shot = copy.copy(descriptor.gun.shots[0])
        second_shot.shell = copy.copy(second_shot.shell)
        second_shot.shell.compactDescr = 102
        descriptor.gun.shots = tuple(descriptor.gun.shots) + (second_shot,)
        entity = _Vehicle(
            11, descriptor, _Vector(50.0, 0.0, 50.0), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities[11] = entity
        effective = _effective_params_snapshot(
            ammo=[[101, 20], [102, 20]])
        record = {
            'engine_id': 11, 'network_id': 2, 'kind': 'player',
            'local': False, 'ready': True, 'tombstone': False,
            'state': {
                'alive': True, 'shell_index': 0,
                'next_shell_index': 1, 'shell_change_pending': True,
                'speed': 0.0, 'turn': 0.0,
                'effective_params': effective,
            },
        }
        battle._records = {'player:2': record}
        gun = gun_mechanics.GunState(
            descriptor, effective['loadout'],
            ammo_layout={101: 20, 102: 20})
        gun.reload_time = 0.0
        gun.clip = 1
        gun._effective_params = effective
        battle._player_authority_guns = {2: gun}
        intent = {
            'type': 'fire_intent', 'round_id': 7,
            'authority_epoch': 4, 'player_id': 2, 'intent_seq': 3,
            'shot_seq': 5, 'input_seq': 8, 'pose_time_us': 1000,
            'shell_index': 0, 'next_shell_index': 1,
            'shell_change_pending': True,
            'gun_checkpoint_seq': 8,
            'gun_checkpoint': _human_gun_checkpoint(),
            'aim_yaw': 0.2, 'gun_pitch': -0.1,
            'x': 50.0, 'y': 0.0, 'z': 50.0, 'yaw': 0.0,
            'pitch': 0.0, 'roll': 0.0, 'speed': 0.0,
            'shot_origin': [4.0, 2.0, 8.0],
            'shot_direction': [0.0, 0.0, 1.0],
            'dispersion_angle': 0.02,
        }

        self.assertTrue(battle.on_fire_intent(intent))
        self.assertTrue(battle._advance_player_fire_authority(0.1, 10.0))
        self.assertEqual(1, gun.pending_index)
        self.assertTrue(battle._accept_player_fire_commit({
            'shooter_kind': 'player', 'shooter_id': 2,
            'fire_intent_seq': 3, 'fire_input_seq': 8,
            'shot_seq': 5, 'shell_index': 0,
        }, record))
        self.assertEqual(1, gun.shot_index)
        self.assertIsNone(gun.pending_index)

    def test_worker_applies_intuition_ready_shell_without_restarting_reload(self):
        descriptor = _Descriptor()
        second_shot = copy.copy(descriptor.gun.shots[0])
        second_shot.shell = copy.copy(second_shot.shell)
        second_shot.shell.compactDescr = 102
        descriptor.gun.shots = tuple(descriptor.gun.shots) + (second_shot,)
        gun = gun_mechanics.GunState(
            descriptor, ammo_layout={101: 20, 102: 20})
        gun.reload_time = 4.0
        gun.clip = 0
        intent = {
            'input_seq': 9, 'gun_checkpoint_seq': 9,
            'shell_index': 1, 'next_shell_index': 1,
            'shell_change_pending': False,
            'gun_checkpoint': _human_gun_checkpoint(),
        }

        self.assertTrue(BattleRuntime._apply_player_gun_checkpoint(
            gun, intent))
        self.assertEqual(1, gun.shot_index)
        self.assertEqual(0.0, gun.reload_time)
        self.assertEqual(1, gun.clip)
        self.assertEqual([20, 20], gun.ammo)

    def test_worker_rejects_stale_or_not_ready_human_gun_checkpoint(self):
        gun = gun_mechanics.GunState(
            _Descriptor(), ammo_layout={101: 20})
        not_ready = {
            'input_seq': 9, 'gun_checkpoint_seq': 9,
            'shell_index': 0, 'next_shell_index': 0,
            'shell_change_pending': False,
            'gun_checkpoint': _human_gun_checkpoint(
                reload_time=1.0, clip=0),
        }

        self.assertFalse(BattleRuntime._apply_player_gun_checkpoint(
            gun, not_ready))
        with self.assertRaisesRegex(RuntimeError, 'checkpoint is invalid'):
            BattleRuntime._apply_player_gun_checkpoint(gun, not_ready)

    def test_worker_launch_pending_survives_stall_until_result(self):
        battle = BattleRuntime(_runtime())
        battle._worker_mode = True
        battle._start_message = {'round_id': 7}
        battle._player_fire_launch_pending = {2: {
            'intent_seq': 3, 'input_seq': 4, 'shot_seq': 5,
            'sent_at': 1.0,
        }}
        battle._projectile_is_authority = lambda: True

        self.assertTrue(battle._advance_player_fire_authority(0.1, 10.0))
        self.assertIn(2, battle._player_fire_launch_pending)

        self.assertTrue(battle.on_fire_intent_result({
            'type': 'fire_intent_result', 'round_id': 7,
            'player_id': 2, 'intent_seq': 3, 'accepted': False,
            'reason': 'projectile_launch_rejected',
        }))

        self.assertEqual({}, battle._player_fire_launch_pending)
        self.assertTrue(battle._advance_player_fire_authority(0.1, 10.0))

    def test_server_shot_event_confirms_local_after_mailbox_returns(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        local = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                         {'health': 500})
        remote = _Vehicle(11, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities.update({10: local, 11: remote})
        battle._records = {
            'player:1': {'engine_id': 10, 'local': True},
            'player:2': {'engine_id': 11, 'local': False}}

        battle._show_shot({'attacker': 1})
        battle._show_shot({'attacker': 2, 'shell_index': 2})

        self.assertEqual((1, False), local.last_shot)
        self.assertEqual((1, False), remote.last_shot)
        self.assertEqual(2, remote._offlineLANShotIndex)
        self.assertEqual(10.75,
                         battle._records['player:1']['shot_penalty_until'])
        self.assertEqual(10.75,
                         battle._records['player:2']['shot_penalty_until'])

    def test_bot_shot_camouflage_penalty_does_not_mark_same_id_player(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        player = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        bot = _Vehicle(11, _Descriptor(), _Vector(), (0, 0, 0),
                       {'health': 500})
        runtime.bigworld.entities.update({10: player, 11: bot})
        battle._records = {
            'player:1': {'engine_id': 10, 'local': True},
            'bot:1': {'engine_id': 11, 'local': False}}

        battle._show_shot({'attacker_bot': 1})

        self.assertEqual((1, False), bot.last_shot)
        self.assertFalse(hasattr(player, 'last_shot'))
        self.assertNotIn('shot_penalty_until', battle._records['player:1'])
        self.assertEqual(10.75,
                         battle._records['bot:1']['shot_penalty_until'])

    def test_server_shot_uses_finite_descriptor_burst(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        entity.typeDescriptor.gun.burst = (3, 0.05)
        runtime.bigworld.entities[10] = entity
        battle._records = {
            'player:1': {'engine_id': 10, 'local': True}}

        battle._show_shot({'attacker': 1})

        self.assertEqual((3, False), entity.last_shot)

    def test_invalid_server_shot_burst_falls_back_to_one(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        entity.typeDescriptor.gun.burst = (0,)
        runtime.bigworld.entities[10] = entity
        battle._records = {
            'player:1': {'engine_id': 10, 'local': True}}

        battle._show_shot({'attacker': 1})

        self.assertEqual((1, False), entity.last_shot)

    def test_remote_pose_updates_exact_packed_gun_angles(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        entity = _Vehicle(11, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[11] = entity
        battle._records = {
            'player:2': {'engine_id': 11, 'state': {'health': 500},
                         'kind': 'player', 'network_id': 2, 'local': False}}

        battle._update_entity({
            'entity': 'player:2', 'kind': 'player', 'id': 2,
            'pose': {'x': 4.0, 'y': 0.0, 'z': 8.0, 'yaw': 3.0,
                     'aim_yaw': -3.0, 'gun_pitch': -0.15},
            'state': {'health': 500}})

        battle._binding.update_vehicle_aim.assert_called_once_with(
            11, 3.0, -3.0, -0.15)
        pose_call = battle._binding.set_vehicle_pose.call_args
        self.assertEqual(11, pose_call[0][0])
        self.assertEqual((4.0, 0.0, 8.0), tuple(pose_call[0][1]))
        self.assertEqual((0.0, 0.0, 3.0), pose_call[0][2])
        self.assertEqual(runtime.bigworld.now, pose_call[1]['now'])

    def test_render_pose_fast_path_skips_materialization_and_bounds_tracks(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        now = [10.0]
        battle._clock = lambda: now[0]
        vehicle = types.SimpleNamespace(
            typeDescriptor=_Descriptor(), track_scroll=object(),
            update_tracks=mock.Mock(return_value=True), bw_entity_id=11,
            settle_motion=mock.Mock(return_value=True),
            track_scroll_readback=mock.Mock(
                return_value=(1.0, 1.0, True, True)))
        battle._remote_factory = types.SimpleNamespace(
            get=lambda entity_id: vehicle if entity_id == 11 else None,
            track_animation_error=None)
        battle._report_bot_tracks = mock.Mock(return_value=True)
        state = {'id': 17, 'health': 500, 'alive': True, 'speed': 8.0}
        record = {
            'engine_id': 11, 'state': state, 'kind': 'bot',
            'network_id': 17, 'local': False, 'ready': True,
            'native_remote': True,
            'track_params': {'trackCenter': 1.0, 'speedFwd': 50.0}}
        battle._records = {'bot:17': record}
        battle._materialize_record = mock.Mock()

        fps = 120
        for frame in range(fps):
            now[0] = 10.0 + frame / float(fps)
            battle._update_entity({
                'type': 'update', 'entity': 'bot:17', 'kind': 'bot',
                'id': 17, 'remote': True, 'interpolated': True,
                'presentation_time_us': frame * 1000000 // fps,
                'pose': {
                    'x': 0.0, 'y': 0.0,
                    'z': (frame + 1) / float(fps),
                    'yaw': 0.0, 'pitch': 0.0, 'roll': 0.0,
                    'aim_yaw': 0.0, 'gun_pitch': 0.0}})

        # Hull and aim remain render-rate smooth; only the native 20 Hz belt
        # controller is rate limited, and no pose-only sample walks the full
        # state/materialization path.
        self.assertEqual(fps, battle._binding.set_vehicle_pose.call_count)
        self.assertEqual(fps, battle._binding.update_vehicle_aim.call_count)
        self.assertEqual(20, vehicle.update_tracks.call_count)
        battle._materialize_record.assert_not_called()
        self.assertIs(state, record['state'])

        # Replaying an identical render pose still advances the receipt time
        # and may finish one due 20 Hz belt feed, but performs no full native
        # pose or aim write.  A confirmed-history underrun can repeat a pose
        # while authority still says the bot is moving; that is a playback
        # hold, not a reason to zero native body motion.
        now[0] = 11.0
        previous_track_calls = vehicle.update_tracks.call_count
        battle._update_entity({
            'type': 'update', 'entity': 'bot:17', 'kind': 'bot',
            'id': 17, 'remote': True, 'interpolated': True,
            'presentation_time_us': 1000000,
            'pose': {
                'x': 0.0, 'y': 0.0, 'z': 1.0,
                'yaw': 0.0, 'pitch': 0.0, 'roll': 0.0,
                'aim_yaw': 0.0, 'gun_pitch': 0.0}})
        self.assertEqual(fps, battle._binding.set_vehicle_pose.call_count)
        self.assertEqual(fps, battle._binding.update_vehicle_aim.call_count)
        self.assertEqual(previous_track_calls + 1,
                         vehicle.update_tracks.call_count)
        vehicle.settle_motion.assert_not_called()
        previous_track_calls = vehicle.update_tracks.call_count
        self.assertEqual(1000000, record['presentation_time_us'])

        # A speed edge at an unchanged pose must still stop the native belts.
        # Pose-only duplicates remain free above, while a new snapshot state
        # is part of the track presentation identity.
        now[0] = 11.01
        record['state'] = dict(state, speed=0.0)
        battle._apply_record_pose(record, {
            'x': 0.0, 'y': 0.0, 'z': 1.0,
            'yaw': 0.0, 'pitch': 0.0, 'roll': 0.0,
            'aim_yaw': 0.0, 'gun_pitch': 0.0})
        vehicle.settle_motion.assert_called_once_with(11.01)
        self.assertEqual(fps,
                         battle._binding.set_vehicle_pose.call_count)
        self.assertEqual(previous_track_calls,
                         vehicle.update_tracks.call_count)
        now[0] = 11.05
        battle._apply_record_pose(record, {
            'x': 0.0, 'y': 0.0, 'z': 1.0,
            'yaw': 0.0, 'pitch': 0.0, 'roll': 0.0,
            'aim_yaw': 0.0, 'gun_pitch': 0.0})
        self.assertEqual(fps,
                         battle._binding.set_vehicle_pose.call_count)
        self.assertEqual(previous_track_calls + 1,
                         vehicle.update_tracks.call_count)
        vehicle.settle_motion.assert_called_once_with(11.01)
        self.assertEqual((0.0, 0.0),
                         vehicle.update_tracks.call_args[0][:2])

    def test_remote_tracks_retry_a_failed_native_feed_without_scroll(self):
        battle = BattleRuntime(_runtime())
        battle._binding = mock.Mock()
        now = [10.0]
        battle._clock = lambda: now[0]
        vehicle = types.SimpleNamespace(
            typeDescriptor=_Descriptor(), track_scroll=None,
            update_tracks=mock.Mock(side_effect=(False, True)),
            bw_entity_id=11)
        battle._remote_factory = types.SimpleNamespace(
            get=lambda entity_id: vehicle if entity_id == 11 else None,
            track_animation_error=None)
        battle._report_bot_tracks = mock.Mock(return_value=True)
        record = {
            'engine_id': 11, 'kind': 'bot', 'network_id': 17,
            'local': False,
            'state': {'id': 17, 'health': 500, 'alive': True,
                      'speed': 8.0},
            'track_params': {'trackCenter': 1.0, 'speedFwd': 50.0}}
        pose = {
            'x': 0.0, 'y': 0.0, 'z': 1.0, 'yaw': 0.0,
            'pitch': 0.0, 'roll': 0.0,
            'aim_yaw': 0.0, 'gun_pitch': 0.0}

        battle._apply_record_pose(record, pose)

        self.assertTrue(record['_remote_track_pending'])
        self.assertNotIn('_remote_track_state_signature', record)
        self.assertEqual(1, vehicle.update_tracks.call_count)

        now[0] = 10.05
        battle._apply_record_pose(record, pose)

        self.assertFalse(record['_remote_track_pending'])
        self.assertIn('_remote_track_state_signature', record)
        self.assertEqual(2, vehicle.update_tracks.call_count)

    def test_remote_tracks_stop_after_the_last_turning_pose(self):
        battle = BattleRuntime(_runtime())
        battle._binding = mock.Mock()
        now = [10.0]
        battle._clock = lambda: now[0]
        vehicle = types.SimpleNamespace(
            typeDescriptor=_Descriptor(), track_scroll=object(),
            update_tracks=mock.Mock(return_value=True), bw_entity_id=11)
        battle._remote_factory = types.SimpleNamespace(
            get=lambda entity_id: vehicle if entity_id == 11 else None,
            track_animation_error=None)
        battle._report_bot_tracks = mock.Mock(return_value=True)
        record = {
            'engine_id': 11, 'kind': 'bot', 'network_id': 17,
            'local': False,
            'state': {'id': 17, 'health': 500, 'alive': True,
                      'speed': 0.0},
            'track_params': {'trackCenter': 1.0, 'speedFwd': 50.0}}
        still = {
            'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0,
            'pitch': 0.0, 'roll': 0.0,
            'aim_yaw': 0.0, 'gun_pitch': 0.0}

        battle._apply_record_pose(record, still)
        now[0] = 10.05
        turning = dict(still, yaw=0.05, aim_yaw=0.05)
        battle._apply_record_pose(record, turning)
        self.assertEqual(2, vehicle.update_tracks.call_count)
        self.assertEqual(
            (ENGINE_MODE_RUNNING, _MOVEMENT_ROTATE_RIGHT),
            vehicle.update_tracks.call_args.args[2])

        # The first still frame is inside the native 20 Hz window. It must
        # stay pending and retry at the deadline, or rotate mode sticks.
        now[0] = 10.058
        battle._apply_record_pose(record, turning)
        self.assertEqual(2, vehicle.update_tracks.call_count)
        now[0] = 10.1
        battle._apply_record_pose(record, turning)
        self.assertEqual(3, vehicle.update_tracks.call_count)
        self.assertEqual((0.0, 0.0),
                         vehicle.update_tracks.call_args.args[:2])
        self.assertEqual((ENGINE_MODE_IDLE, 0),
                         vehicle.update_tracks.call_args.args[2])

    def test_remote_update_is_coalesced_until_vehicle_materializes(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._server = types.SimpleNamespace()
        battle._binding = mock.Mock()
        battle._binding.is_vehicle_ready.side_effect = lambda entity_id: (
            runtime.bigworld.entity(entity_id) is not None)
        battle._records = {
            'player:2': {
                'engine_id': 11, 'state': {'health': 500},
                'kind': 'player', 'network_id': 2, 'local': False,
                'ready': False, 'ready_deadline': runtime.bigworld.now + 5.0}}

        battle._update_entity({
            'entity': 'player:2', 'kind': 'player', 'id': 2,
            'pose': {'x': 4.0, 'y': 0.0, 'z': 8.0, 'yaw': 0.5,
                     'aim_yaw': 0.7, 'gun_pitch': -0.1},
            'state': {'health': 125}})

        battle._binding.set_vehicle_pose.assert_not_called()
        battle._binding.update_vehicle_aim.assert_not_called()
        self.assertNotIn(11, battle._last_health)

        entity = _Vehicle(11, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[11] = entity
        battle._flush_pending_entities(runtime.bigworld.now)

        self.assertTrue(battle._records['player:2']['ready'])
        pose_call = battle._binding.set_vehicle_pose.call_args
        self.assertEqual(11, pose_call[0][0])
        self.assertEqual((4.0, 0.0, 8.0), tuple(pose_call[0][1]))
        battle._binding.update_vehicle_aim.assert_called_once_with(
            11, 0.5, 0.7, -0.1)
        self.assertEqual(125, entity.health)
        self.assertEqual((125, 0, 0), entity.health_change)

    def test_pending_remote_death_materializes_as_corpse(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._server = types.SimpleNamespace()
        battle._binding = mock.Mock()
        battle._binding.is_vehicle_ready.side_effect = lambda entity_id: (
            runtime.bigworld.entity(entity_id) is not None)
        battle._records = {
            'bot:2': {
                'engine_id': 11, 'state': {'health': 500, 'alive': True},
                'kind': 'bot', 'network_id': 2, 'local': False,
                'ready': False, 'ready_deadline': runtime.bigworld.now + 5.0}}

        battle._destroy_entity({
            'entity': 'bot:2', 'keep_corpse': True,
            'state': {'health': 0, 'alive': False}})
        self.assertFalse(battle._records['bot:2']['ready'])

        entity = _Vehicle(11, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[11] = entity
        battle._flush_pending_entities(runtime.bigworld.now)

        self.assertEqual(0, entity.health)
        self.assertEqual((0, 0, 0), entity.health_change)
        battle._binding.arena_vehicle_killed.assert_called_once_with(
            11, 0, 0)

    def test_pending_remote_presentation_destroy_cancels_late_load(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._config = {
            'map': '01_karelia',
            'vehicle': 'ussr:R11_MS-1',
            'startupTimeoutSeconds': 30.0}
        battle._server = types.SimpleNamespace()
        battle._binding = mock.Mock()
        battle._binding.properties_from_compact_descr.return_value = {
            'publicInfo': {'compDescr': 'ussr:R11_MS-1'},
            'health': 500}
        battle._remote_factory = mock.Mock()
        battle._remote_factory.prepare_descriptor.side_effect = (
            lambda descriptor: descriptor)
        battle._remote_factory.create.return_value = 1000
        battle._remote_factory.error.return_value = None
        battle._remote_factory.is_ready.return_value = False

        battle._create_remote({
            'type': 'create', 'entity': 'bot:2', 'kind': 'bot', 'id': 2,
        'state': {
            'team': 2, 'slot': 0, 'x': 5.0, 'y': 0.0, 'z': 5.0,
            'world_pose': True,
            'vehicle': 'ussr:R11_MS-1', 'health': 500}})
        record = battle._records['bot:2']
        self.assertEqual(1000, record['engine_id'])
        self.assertTrue(record['presentation'])
        self.assertFalse(record['ready'])

        battle._destroy_entity({'entity': 'bot:2'})
        self.assertNotIn('bot:2', battle._records)
        battle._remote_factory.destroy.assert_called_once_with(1000)
        battle._binding.destroy_entity.assert_not_called()

    def test_terminal_result_notifies_native_hud_once_with_finish_reason(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle.state = 'running'

        battle.on_events({'events': [{
            'event_id': '1:1:0',
            'kind': 'battle_result', 'winner': 2,
            'reason': 'team_eliminated'}]})
        battle.on_snapshot({'battle_result': {
            'winner': 2, 'reason': 'team_eliminated'}})

        self.assertEqual(
            [(2, runtime.constants.FINISH_REASON.EXTERMINATION)],
            runtime.bigworld.avatar.round_finished)
        self.assertTrue(battle._round_finished_notified)

    def test_worker_terminal_result_stops_simulation_without_hud(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._worker_mode = True
        battle._avatar = runtime.bigworld.avatar
        battle._battle_live = True
        battle.state = 'running'

        self.assertTrue(battle._apply_battle_result({
            'winner': 2, 'reason': 'battle_timeout'}))

        self.assertFalse(battle._battle_live)
        self.assertTrue(battle._round_finished_notified)
        self.assertEqual([], runtime.bigworld.avatar.round_finished)

    def test_base_capture_uses_exact_1513_event_shapes(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle.state = 'running'

        self.assertTrue(battle._apply_rules({'bases': {
            '1': {'points': 42, 'time_left': 29.0,
                  'invaders': 2, 'stopped': True},
            '2': {'points': 0, 'time_left': 0.0,
                  'invaders': 0, 'stopped': False}}}))
        self.assertEqual([
            (1, 0, 42, 29.0, 2, True),
            (2, 0, 0, 0.0, 0, False),
        ], runtime.bigworld.avatar.base_points)

        self.assertTrue(battle._apply_rules({'bases': {
            '1': {'points': 42, 'time_left': 27.5,
                  'invaders': 3, 'stopped': True},
            '2': {'points': 0, 'time_left': 0.0,
                  'invaders': 0, 'stopped': False}}}))
        self.assertEqual(
            (1, 0, 42, 27.5, 3, True),
            runtime.bigworld.avatar.base_points[-1])
        update_count = len(runtime.bigworld.avatar.base_points)
        self.assertFalse(battle._apply_rules({'bases': {
            '1': {'points': 42, 'time_left': 27.5,
                  'invaders': 3, 'stopped': True},
            '2': {'points': 0, 'time_left': 0.0,
                  'invaders': 0, 'stopped': False}}}))
        self.assertEqual(update_count,
                         len(runtime.bigworld.avatar.base_points))

        self.assertTrue(battle._apply_battle_result({
            'winner': 2, 'reason': 'base captured', 'base_team': 1}))
        self.assertEqual([(1, 0)], runtime.bigworld.avatar.base_captured)
        self.assertEqual([
            (2, runtime.constants.FINISH_REASON.BASE),
        ], runtime.bigworld.avatar.round_finished)

    def test_ammo_hud_producer_obeys_exact_integer_wire_ranges(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._avatar = runtime.bigworld.avatar
        descriptor = _Descriptor()
        descriptor.gun.maxAmmo = 999999
        descriptor.gun.clip = (999,)
        descriptor.gun.reloadTime = 1.5
        entity = _Vehicle(10, descriptor, _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._garage_loadout_snapshot()
        battle._garage_loadout['shells'] = {101: 999999}

        battle._ammo_tick()

        update = runtime.bigworld.avatar.ammo_updates[0]
        self.assertEqual(5, len(update))
        self.assertTrue(all(isinstance(value, int) for value in update))
        self.assertEqual(65535, update[2])
        # Exact 0.8.2 starts with an empty breech/magazine and begins the
        # first reload only after the battle period becomes live.
        self.assertEqual(0, update[3])
        self.assertEqual(0, update[4])

        battle._gun_last_tick -= 2.0
        battle._ammo_tick()
        loaded = runtime.bigworld.avatar.ammo_updates[-1]
        self.assertEqual(255, loaded[3])
        self.assertEqual(0, loaded[4])

    def test_reload_hud_receives_edges_and_interpolates_between_them(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._battle_live = False
        battle._config = {}
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        entity = _Vehicle(
            10, _Descriptor(), _Vector(), (0, 0, 0), {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)

        battle._ammo_tick()
        battle._ammo_tick()
        self.assertEqual(1, len(runtime.bigworld.avatar.reload_updates))
        self.assertEqual(0.0, runtime.bigworld.avatar.reload_updates[0][1])

        battle._begin_battle()
        self.assertEqual(2, len(runtime.bigworld.avatar.reload_updates))
        self.assertGreater(runtime.bigworld.avatar.reload_updates[1][1], 0.0)

        runtime.bigworld.now += 0.5
        battle._ammo_tick()
        self.assertEqual(2, len(runtime.bigworld.avatar.reload_updates))
        runtime.bigworld.now += 2.0
        battle._ammo_tick()
        self.assertEqual(3, len(runtime.bigworld.avatar.reload_updates))
        self.assertEqual(0.0, runtime.bigworld.avatar.reload_updates[-1][1])

    def test_client_ready_does_not_start_a_second_ammo_timer(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._avatar = runtime.bigworld.avatar
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = mock.Mock()
        runtime.bigworld.entities[10] = _Vehicle(
            10, _Descriptor(), _Vector(), (0, 0, 0), {'health': 500})

        battle._ammo_tick()
        callbacks = len(runtime.bigworld.callbacks)
        battle._on_client_ready()

        self.assertEqual(callbacks, len(runtime.bigworld.callbacks))
        battle._sender.send_current.assert_called_once_with()

    def test_ammo_tick_never_writes_read_only_1513_dispersion_property(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._avatar = runtime.bigworld.avatar
        descriptor = _Descriptor()
        entity = _Vehicle(10, descriptor, _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)

        class ReadOnlyGunRotator(object):
            @property
            def dispersionAngle(self):
                return 0.25

        runtime.bigworld.avatar.gunRotator = ReadOnlyGunRotator()

        battle._ammo_tick()

        self.assertEqual('running', battle.state)
        self.assertIsNone(battle.error)

    def test_ammo_tick_feeds_native_1513_dispersion_parameters(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._avatar = runtime.bigworld.avatar
        descriptor = _Descriptor()
        entity = _Vehicle(10, descriptor, _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)

        battle._ammo_tick()

        targeting = runtime.bigworld.avatar.targeting
        crew_multiplier = 1.0 / (0.57 + 0.0043 * 110.0)
        self.assertAlmostEqual(crew_multiplier, targeting[4])
        self.assertEqual(0.1, targeting[5])
        self.assertEqual(0.14, targeting[6])
        self.assertEqual(0.14, targeting[7])
        self.assertAlmostEqual(crew_multiplier, targeting[8])

    def test_damaged_turret_rotator_scales_native_traverse_speed(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._avatar = runtime.bigworld.avatar
        descriptor = _Descriptor()
        entity = _Vehicle(10, descriptor, _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)

        def factor(unused_entity, stat):
            return 0.5 if stat == 'turret_speed' else 1.0

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'critical_damage.stat_factor', side_effect=factor):
            battle._ammo_tick()

        # updateTargetingInfo takes the final speed, so the gunner factor is
        # applied on top of the damage factor.
        self.assertAlmostEqual(
            descriptor.turret.rotationSpeed * 0.5 * (0.57 + 0.0043 * 110.0),
            runtime.bigworld.avatar.targeting[2])

    def test_ammo_rack_penalty_rescales_the_current_reload_progress(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._battle_live = True
        battle._avatar = runtime.bigworld.avatar
        descriptor = _Descriptor()
        entity = _Vehicle(
            10, descriptor, _Vector(), (0, 0, 0), {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        state = gun_mechanics.GunState(descriptor)
        state.reload = 4.0
        state.clip = 0
        state.reload_time = 2.0
        state.reload_duration = 4.0
        battle._gun_state = state
        battle._gun_last_tick = runtime.bigworld.now
        battle._reload_event = (2.0, 4.0)

        def damaged_factor(unused_entity, stat):
            return 2.0 if stat == 'reload' else 1.0

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'critical_damage.stat_factor', side_effect=damaged_factor):
            battle._ammo_tick()

        self.assertEqual(8.0, state.reload_duration)
        self.assertEqual(4.0, state.reload_time)
        self.assertEqual((10, 4.0, 8.0),
                         runtime.bigworld.avatar.reload_updates[-1])

        battle._gun_last_tick = runtime.bigworld.now
        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'critical_damage.stat_factor', return_value=1.0):
            battle._ammo_tick()

        self.assertEqual(4.0, state.reload_duration)
        self.assertEqual(2.0, state.reload_time)
        self.assertEqual((10, 2.0, 4.0),
                         runtime.bigworld.avatar.reload_updates[-1])

    def test_parsed_1513_light_tank_bloom_uses_raw_descriptor_factor(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._avatar = runtime.bigworld.avatar
        descriptor = _Descriptor()
        # #1513 converts XML 0.14 to per-m/s and per-rad/s runtime values.
        descriptor.chassis.shotDispersionFactors = (0.504, 8.02)
        entity = _Vehicle(10, descriptor, _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)

        battle._ammo_tick()

        targeting = runtime.bigworld.avatar.targeting
        movement_factor = targeting[6]
        full_speed_multiplier = math.sqrt(
            1.0 + (16.67 * movement_factor) ** 2)
        self.assertAlmostEqual(0.504, movement_factor)
        self.assertAlmostEqual(
            math.sqrt(1.0 + (16.67 * 0.504) ** 2),
            full_speed_multiplier)

    def test_targeting_aim_time_matches_real_1513_vehicle_descriptors(self):
        # Exact #1513 Packed XML values for a tier I, V and X mounted gun.
        rows = (
            ('ussr:R11_MS-1/_45mm_mod_1932', 2.5),
            ('ussr:R04_T-34/_76mm_S-54', 2.9),
            ('ussr:R45_IS-7/_130mm_S-70', 2.9),
        )
        gunner_factor = 0.57 + 0.43 * 1.10
        client_factors = {
            'turret/rotationSpeed': gunner_factor,
            'gun/rotationSpeed': gunner_factor,
            'gun/aimingTime': 1.0 / gunner_factor,
            'shotDispersion': [1.0 / gunner_factor, 0.0],
        }
        for name, descriptor_aim_time in rows:
            runtime = _runtime()
            battle = BattleRuntime(runtime)
            battle._avatar = runtime.bigworld.avatar
            descriptor = _Descriptor(name.split('/', 1)[0])
            descriptor.gun.aimingTime = descriptor_aim_time
            descriptor.miscAttrs = {'gunAimingTimeFactor': 1.0}
            state = gun_mechanics.GunState(
                descriptor,
                battle_runtime_module.loadout_law.modifiers(
                    descriptor, factors=client_factors))
            entity = _Vehicle(
                10, descriptor, _Vector(), (0, 0, 0), {'health': 500})

            battle._publish_targeting_info(entity, state)

            self.assertAlmostEqual(
                descriptor_aim_time / gunner_factor,
                runtime.bigworld.avatar.targeting[-1],
                msg=name)

    def test_ammo_tick_does_not_restart_native_gun_rotator_each_frame(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._avatar = runtime.bigworld.avatar
        descriptor = _Descriptor()
        entity = _Vehicle(10, descriptor, _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)

        battle._ammo_tick()
        battle._ammo_tick()

        self.assertEqual(1, len(runtime.bigworld.avatar.targeting_updates))

        descriptor.gun.rotationSpeed = 0.75
        battle._ammo_tick()
        self.assertEqual(2, len(runtime.bigworld.avatar.targeting_updates))
        self.assertAlmostEqual(
            0.75 * (0.57 + 0.0043 * 110.0),
            runtime.bigworld.avatar.targeting_updates[-1][3])

    def test_ammo_tick_keeps_enabled_server_marker_on_the_client_angle(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._battle_live = True
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(
            10, _Descriptor(), _Vector(), (0, 0, 0), {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        shot_position = _Vector(1.0, 2.0, 3.0)
        shot_vector = _Vector(0.0, 0.0, 250.0)
        runtime.bigworld.avatar.gunRotator = types.SimpleNamespace(
            showServerMarker=True,
            dispersionAngle=0.0375,
            getCurShotPosition=mock.Mock(
                return_value=(shot_position, shot_vector)))

        battle._ammo_tick()

        self.assertEqual([
            (10, shot_position, shot_vector, 0.0375)
        ], runtime.bigworld.avatar.gun_marker_updates)

    def test_native_dispersion_uses_read_only_rotator_without_class_patch(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        avatar_type = type(runtime.bigworld.avatar)
        original = avatar_type.__dict__[
            'getOwnVehicleShotDispersionAngle']
        battle._avatar.gunRotator = types.SimpleNamespace(
            dispersionAngle=0.0375)

        self.assertAlmostEqual(0.0375, battle._native_dispersion_angle())
        self.assertIs(
            original,
            avatar_type.__dict__['getOwnVehicleShotDispersionAngle'])

    def test_invalid_native_dispersion_fails_without_fallback(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._avatar.gunRotator = types.SimpleNamespace(
            dispersionAngle=float('nan'))

        with self.assertRaisesRegex(RuntimeError, 'angle is invalid'):
            battle._native_dispersion_angle()

    def test_equipment_activation_decodes_extra_and_enters_cooldown(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._avatar = runtime.bigworld.avatar
        send_intent = mock.Mock(side_effect=(1, 2, 3, 4))
        battle.client = types.SimpleNamespace(
            player_id=1, send_equipment_intent=send_intent)
        descriptor = _Descriptor()
        extra = types.SimpleNamespace(name='engineHealth')
        descriptor.extras = {7: extra}
        descriptor.extrasDict = {'engineHealth': extra}
        descriptor.engine = {'maxHealth': 100, 'maxRegenHealth': 50}
        entity = _Vehicle(10, descriptor, _Vector(), (0, 0, 0),
                          {'health': 500})
        entity.devices_hp = {'engineHealth': 0.0}
        entity._destroyed_devices = set(['engineHealth'])
        entity._crew_ko = set()
        entity.is_on_fire = False
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._records = {'player:1': {
            'engine_id': 10, 'state': {'health': 500, 'alive': True},
            'kind': 'player', 'network_id': 1, 'local': True}}
        repairkit = types.SimpleNamespace(
            id=(11, 41), compactDescr=401,
            name='smallRepairkit', tags=('repairkit',),
            cooldownSeconds=90.0, reuseCount=-1, repairAll=False)
        battle._equipment_state = [equipment_mechanics.EquipmentState(
            equipment_mechanics.project_equipment(repairkit))]
        battle._present_critical = mock.Mock(return_value=True)
        clock = [1000.0]
        battle._clock = lambda: clock[0]

        activation_code = (7 << 16) | 41
        battle._records['player:1']['state'].update(
            health=0, alive=False, display_health=500)
        self.assertTrue(battle.change_vehicle_setting(
            runtime.constants.VEHICLE_SETTING.ACTIVATE_EQUIPMENT,
            activation_code))
        self.assertEqual(0.0, entity.devices_hp['engineHealth'])
        self.assertEqual(0.0, battle._equipment_state[0].ready_at)
        battle._records['player:1']['state'].update(
            health=500, alive=True)
        self.assertTrue(battle.change_vehicle_setting(
            runtime.constants.VEHICLE_SETTING.ACTIVATE_EQUIPMENT,
            activation_code))

        self.assertEqual(0.0, entity.devices_hp['engineHealth'])
        self.assertEqual(0.0, battle._equipment_state[0].ready_at)
        self.assertNotIn('critical_state', battle._records['player:1'])
        self.assertIsNone(battle._local_damage_report)

        # Readiness, life and inventory are canonical server decisions; the
        # visible bridge relays the immutable selection intent only.
        self.assertTrue(battle.change_vehicle_setting(
            runtime.constants.VEHICLE_SETTING.ACTIVATE_EQUIPMENT,
            activation_code))

        # Once it expires the kit is READY and usable again.
        clock[0] = 1090.0
        battle._tick_equipment_cooldowns(clock[0])
        self.assertEqual((10, 401, 1, 3, 0),
                         runtime.bigworld.avatar.ammo_updates[-1])
        entity.devices_hp['engineHealth'] = 0.0
        entity._destroyed_devices = set(['engineHealth'])
        self.assertTrue(battle.change_vehicle_setting(
            runtime.constants.VEHICLE_SETTING.ACTIVATE_EQUIPMENT,
            activation_code))
        self.assertEqual(0.0, entity.devices_hp['engineHealth'])
        self.assertEqual(4, send_intent.call_count)
        for call in send_intent.call_args_list:
            self.assertEqual(
                mock.call(41, activation_code=activation_code,
                          selected='engineHealth',
                          requested_active=None), call)

    def test_manual_extinguisher_does_not_send_extra_zero_as_a_target(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        send_intent = mock.Mock(return_value=1)
        battle.client = types.SimpleNamespace(
            player_id=1, send_equipment_intent=send_intent)
        descriptor = _Descriptor()
        descriptor.extras = {0: types.SimpleNamespace(name='fire')}
        entity = _Vehicle(10, descriptor, _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._records = {'player:1': {
            'engine_id': 10, 'state': {'health': 500, 'alive': True},
            'kind': 'player', 'network_id': 1, 'local': True}}
        extinguisher = types.SimpleNamespace(
            id=(11, 42), compactDescr=402, name='handExtinguishers',
            tags=(), reuseCount=0, cooldownSeconds=0.0,
            autoactivate=False)
        battle._equipment_state = [equipment_mechanics.EquipmentState(
            equipment_mechanics.project_equipment(extinguisher))]

        self.assertTrue(battle._activate_equipment(42))
        send_intent.assert_called_once_with(
            42, activation_code=42, selected=None,
            requested_active=None)

    def test_hit_resolution_uses_public_1513_gun_rotator_api(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._start_message = {'players': [{'id': 1, 'team': 1}]}
        source = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        target = _Vehicle(11, _Descriptor(), _Vector(0, 0, 20), (0, 0, 0),
                          {'health': 500})
        target.collideSegmentExt = lambda start, end: [types.SimpleNamespace(
            dist=20.0, hitAngleCos=1.0,
            matInfo=types.SimpleNamespace(armor=10.0),
            compName='vehicleHull')]
        runtime.bigworld.entities.update({10: source, 11: target})
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._records = {
            'player:1': {'engine_id': 10, 'state': {'team': 1},
                         'kind': 'player', 'network_id': 1, 'local': True},
            'bot:2': {'engine_id': 11,
                      'state': {'team': 2, 'combat_base_revision': 7,
                                'combat_ack_seq': 3},
                      'kind': 'bot', 'network_id': 2, 'local': False}}
        get_shot = mock.Mock(return_value=(
            _Vector(0, 2, 0), _Vector(0, 0, 1)))
        runtime.bigworld.avatar.gunRotator = types.SimpleNamespace(
            getCurShotPosition=get_shot)
        battle.client = types.SimpleNamespace(
            player_id=1, send_bot_hit=mock.Mock(return_value=True))
        battle._shell_damage = mock.Mock(return_value=(120, 2))
        battle._critical_hit = lambda *args, **kwargs: (
            500, {'events': []},
            {'devices': [], 'crew_ko': [], 'ignite': False})

        battle._resolve_hit(7, 0.0, 0.0)

        get_shot.assert_called_once_with()
        battle.client.send_bot_hit.assert_called_once()
        sent = battle.client.send_bot_hit.call_args
        self.assertEqual(500, sent.args[2])
        self.assertEqual(120, sent.kwargs['hull_damage'])
        self.assertEqual(7, sent.kwargs[
            'critical_target_base_revision'])
        self.assertEqual(3, sent.kwargs['critical_target_ack_seq'])

    def test_player_shot_collision_contract_failure_is_not_a_silent_miss(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        source = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        target = _Vehicle(11, _Descriptor(), _Vector(0, 0, 20),
                          (0, 0, 0), {'health': 500})
        target.collideSegmentExt = mock.Mock(
            side_effect=RuntimeError('remote collision failed'))
        runtime.bigworld.entities.update({10: source, 11: target})
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._records = {
            'player:1': {'engine_id': 10, 'kind': 'player',
                         'network_id': 1, 'local': True},
            'bot:2': {'engine_id': 11,
                      'state': {'combat_base_revision': 7,
                                'combat_ack_seq': 3}, 'kind': 'bot',
                      'network_id': 2, 'local': False}}

        with self.assertRaisesRegex(RuntimeError, 'remote collision failed'):
            battle._resolve_hit(7, 0.0, 0.0)

    def test_vehicle_caps_destructible_submission_before_prop_behind_it(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        source = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        target = _Vehicle(11, _Descriptor(), _Vector(0, 0, 5),
                          (0, 0, 0), {'health': 500})
        target.collideSegmentExt = lambda start, end: [types.SimpleNamespace(
            dist=5.0, hitAngleCos=1.0,
            matInfo=types.SimpleNamespace(armor=10.0),
            compName='vehicleHull')]
        runtime.bigworld.entities.update({10: source, 11: target})
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._records = {
            'player:1': {'engine_id': 10, 'kind': 'player',
                         'network_id': 1, 'local': True},
            'bot:2': {'engine_id': 11,
                      'state': {'combat_base_revision': 7,
                                'combat_ack_seq': 3}, 'kind': 'bot',
                      'network_id': 2, 'local': False}}
        runtime.bigworld.avatar.gunRotator = types.SimpleNamespace(
            getCurShotPosition=lambda: (
                _Vector(0, 0, 0), _Vector(0, 0, 1)))
        destroyed = []

        def shot_world_distance(unused_bigworld, unused_space_id,
                                start, end, unused_direction, unused_shot):
            if (end - start).length >= 10.0:
                destroyed.append(('fragile', 10.0))
            return {'world_distance': 999999.0, 'piercing_loss': 0.0,
                    'stop_distance': None, 'continue_from': None}

        battle._destructibles = types.SimpleNamespace(
            shot_world_distance=mock.Mock(side_effect=shot_world_distance))
        battle.client = types.SimpleNamespace(
            player_id=1, send_bot_hit=mock.Mock(return_value=True))
        battle._shell_damage = mock.Mock(return_value=(120, 2))
        battle._critical_hit = lambda *args, **kwargs: (
            args[5], {'events': []},
            {'devices': [], 'crew_ko': [], 'ignite': False})

        battle._resolve_hit(7, 0.0, 0.0)

        self.assertEqual([], destroyed)
        ray = battle._destructibles.shot_world_distance.call_args[0]
        self.assertAlmostEqual(5.0, (ray[3] - ray[2]).length)
        battle.client.send_bot_hit.assert_called_once()

    def test_fragile_before_vehicle_is_destroyed_and_shell_continues(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        source = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        target = _Vehicle(11, _Descriptor(), _Vector(0, 0, 5),
                          (0, 0, 0), {'health': 500})
        target.collideSegmentExt = lambda start, end: [types.SimpleNamespace(
            dist=5.0, hitAngleCos=1.0,
            matInfo=types.SimpleNamespace(armor=10.0),
            compName='vehicleHull')]
        runtime.bigworld.entities.update({10: source, 11: target})
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._records = {
            'player:1': {'engine_id': 10, 'kind': 'player',
                         'network_id': 1, 'local': True},
            'bot:2': {'engine_id': 11,
                      'state': {'combat_base_revision': 7,
                                'combat_ack_seq': 3}, 'kind': 'bot',
                      'network_id': 2, 'local': False}}
        runtime.bigworld.avatar.gunRotator = types.SimpleNamespace(
            getCurShotPosition=lambda: (
                _Vector(0, 0, 0), _Vector(0, 0, 1)))
        destroyed = []

        def shot_world_distance(unused_bigworld, unused_space_id,
                                start, end, unused_direction, unused_shot):
            if (end - start).length >= 4.0:
                destroyed.append(('fragile', 4.0))
            # A dynamic-only fragile has no surviving world collision after
            # destruction, so the shell remains free to reach the vehicle.
            return {'world_distance': 999999.0, 'piercing_loss': 25.0,
                    'stop_distance': None, 'continue_from': None}

        battle._destructibles = types.SimpleNamespace(
            shot_world_distance=mock.Mock(side_effect=shot_world_distance))
        battle.client = types.SimpleNamespace(
            player_id=1, send_bot_hit=mock.Mock(return_value=True))
        battle._shell_damage = mock.Mock(return_value=(120, 2))
        battle._critical_hit = lambda *args, **kwargs: (
            args[5], {'events': []},
            {'devices': [], 'crew_ko': [], 'ignite': False})

        battle._resolve_hit(7, 0.0, 0.0)

        self.assertEqual([('fragile', 4.0)], destroyed)
        ray = battle._destructibles.shot_world_distance.call_args[0]
        self.assertAlmostEqual(5.0, (ray[3] - ray[2]).length)
        battle.client.send_bot_hit.assert_called_once()
        self.assertEqual(
            25.0, battle._shell_damage.call_args.kwargs['pierce_loss'])

    def test_multiple_ap_destructibles_accumulate_before_vehicle(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        responses = [
            {'world_distance': 999999.0, 'piercing_loss': 25.0,
             'stop_distance': None, 'continue_from': 2.0},
            {'world_distance': 999999.0, 'piercing_loss': 25.0,
             'stop_distance': None, 'continue_from': 2.0},
            {'world_distance': 999999.0, 'piercing_loss': 0.0,
             'stop_distance': None, 'continue_from': None},
        ]
        battle._destructibles = types.SimpleNamespace(
            shot_world_distance=mock.Mock(side_effect=responses))

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'combat_rules.sample_penetration_factor',
                return_value=1.0) as draw:
            result = battle._resolve_shot_scene(
                _Vector(), _Vector(0, 0, 5), _Vector(0, 0, 1),
                _Descriptor().gun.shots[0])

        draw.assert_called_once_with()
        self.assertEqual(50.0, result['piercing_loss'])
        self.assertEqual(1.0, result['penetration_factor'])
        self.assertEqual(999999.0, result['world_distance'])
        calls = battle._destructibles.shot_world_distance.call_args_list
        self.assertEqual(3, len(calls))
        self.assertAlmostEqual(0.0, calls[0].args[2].z)
        self.assertAlmostEqual(2.0, calls[1].args[2].z)
        self.assertAlmostEqual(4.0, calls[2].args[2].z)

    def test_thick_native_module_exit_still_reaches_capped_vehicle(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        responses = [
            {'world_distance': 999999.0, 'piercing_loss': 25.0,
             'stop_distance': None, 'continue_from': 8.075},
            {'world_distance': 999999.0, 'piercing_loss': 0.0,
             'stop_distance': None, 'continue_from': None},
        ]
        battle._destructibles = types.SimpleNamespace(
            shot_world_distance=mock.Mock(side_effect=responses))

        result = battle._resolve_shot_scene(
            _Vector(), _Vector(0, 0, 10), _Vector(0, 0, 1),
            _Descriptor().gun.shots[0])

        self.assertEqual(25.0, result['piercing_loss'])
        self.assertEqual(999999.0, result['world_distance'])
        calls = battle._destructibles.shot_world_distance.call_args_list
        self.assertEqual(2, len(calls))
        self.assertAlmostEqual(8.075, calls[1].args[2].z)
        self.assertAlmostEqual(10.0, calls[1].args[3].z)

    def test_thick_native_module_before_vehicle_preserves_vehicle_hit(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        source = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        target = _Vehicle(11, _Descriptor(), _Vector(0, 0, 10),
                          (0, 0, 0), {'health': 500})
        target.collideSegmentExt = lambda start, end: [types.SimpleNamespace(
            dist=10.0, hitAngleCos=1.0,
            matInfo=types.SimpleNamespace(armor=10.0),
            compName='vehicleHull')]
        runtime.bigworld.entities.update({10: source, 11: target})
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._records = {
            'player:1': {'engine_id': 10, 'kind': 'player',
                         'network_id': 1, 'local': True},
            'bot:2': {'engine_id': 11,
                      'state': {'combat_base_revision': 7,
                                'combat_ack_seq': 3}, 'kind': 'bot',
                      'network_id': 2, 'local': False}}
        runtime.bigworld.avatar.gunRotator = types.SimpleNamespace(
            getCurShotPosition=lambda: (
                _Vector(), _Vector(0, 0, 1)))
        responses = [
            {'world_distance': 999999.0, 'piercing_loss': 25.0,
             'stop_distance': None, 'continue_from': 8.075},
            {'world_distance': 999999.0, 'piercing_loss': 0.0,
             'stop_distance': None, 'continue_from': None},
        ]
        battle._destructibles = types.SimpleNamespace(
            shot_world_distance=mock.Mock(side_effect=responses))
        battle.client = types.SimpleNamespace(
            player_id=1, send_bot_hit=mock.Mock(return_value=True))
        battle._shell_damage = mock.Mock(return_value=(120, 2))
        battle._critical_hit = lambda *args, **kwargs: (
            args[5], {'events': []},
            {'devices': [], 'crew_ko': [], 'ignite': False})

        battle._resolve_hit(7, 0.0, 0.0)

        calls = battle._destructibles.shot_world_distance.call_args_list
        self.assertEqual(2, len(calls))
        self.assertAlmostEqual(8.075, calls[1].args[2].z)
        self.assertAlmostEqual(10.0, calls[1].args[3].z)
        battle.client.send_bot_hit.assert_called_once()
        self.assertEqual(
            25.0, battle._shell_damage.call_args.kwargs['pierce_loss'])

    def test_static_wall_after_thick_module_still_blocks_vehicle(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        source = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        target = _Vehicle(11, _Descriptor(), _Vector(0, 0, 10),
                          (0, 0, 0), {'health': 500})
        target.collideSegmentExt = lambda start, end: [types.SimpleNamespace(
            dist=10.0, hitAngleCos=1.0,
            matInfo=types.SimpleNamespace(armor=10.0),
            compName='vehicleHull')]
        runtime.bigworld.entities.update({10: source, 11: target})
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._records = {
            'player:1': {'engine_id': 10, 'kind': 'player',
                         'network_id': 1, 'local': True},
            'bot:2': {'engine_id': 11, 'state': {'health': 500},
                      'kind': 'bot', 'network_id': 2, 'local': False}}
        runtime.bigworld.avatar.gunRotator = types.SimpleNamespace(
            getCurShotPosition=lambda: (
                _Vector(), _Vector(0, 0, 1)))
        responses = [
            {'world_distance': 999999.0, 'piercing_loss': 25.0,
             'stop_distance': None, 'continue_from': 8.075},
            {'world_distance': 0.925, 'piercing_loss': 0.0,
             'stop_distance': 0.925, 'continue_from': None,
             'stopped_by_destructible': False},
        ]
        battle._destructibles = types.SimpleNamespace(
            shot_world_distance=mock.Mock(side_effect=responses))
        battle.client = types.SimpleNamespace(
            player_id=1, send_bot_hit=mock.Mock())

        battle._resolve_hit(7, 0.0, 0.0)

        self.assertEqual(
            2, battle._destructibles.shot_world_distance.call_count)
        battle.client.send_bot_hit.assert_not_called()

    def test_player_wall_20_cm_before_vehicle_strictly_blocks_hit(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        source = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        target = _Vehicle(11, _Descriptor(), _Vector(0, 0, 10),
                          (0, 0, 0), {'health': 500})
        target.collideSegmentExt = lambda start, end: [types.SimpleNamespace(
            dist=10.0, hitAngleCos=1.0,
            matInfo=types.SimpleNamespace(armor=10.0),
            compName='vehicleHull')]
        runtime.bigworld.entities.update({10: source, 11: target})
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._records = {
            'player:1': {'engine_id': 10, 'kind': 'player',
                         'network_id': 1, 'local': True},
            'bot:2': {'engine_id': 11, 'state': {'health': 500},
                      'kind': 'bot', 'network_id': 2, 'local': False}}
        runtime.bigworld.avatar.gunRotator = types.SimpleNamespace(
            getCurShotPosition=lambda: (
                _Vector(), _Vector(0, 0, 1)))
        battle._destructibles = types.SimpleNamespace(
            shot_world_distance=mock.Mock(return_value={
                'world_distance': 9.8, 'piercing_loss': 0.0,
                'stop_distance': 9.8, 'continue_from': None,
                'stopped_by_destructible': False}))
        battle.client = types.SimpleNamespace(
            player_id=1, send_bot_hit=mock.Mock())

        battle._resolve_hit(7, 0.0, 0.0)

        battle.client.send_bot_hit.assert_not_called()

    def test_bot_wall_20_cm_before_vehicle_strictly_blocks_hit(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        source = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        target = _Vehicle(11, _Descriptor(), _Vector(0, 0, 10),
                          (0, 0, 0), {'health': 500})
        target.collideSegmentExt = lambda start, end: [types.SimpleNamespace(
            dist=10.0, hitAngleCos=1.0,
            matInfo=types.SimpleNamespace(armor=10.0),
            compName='vehicleHull')]
        runtime.bigworld.entities.update({10: source, 11: target})
        source_record = {'engine_id': 10, 'kind': 'bot', 'network_id': 1,
                         'local': False}
        target_record = {'engine_id': 11, 'kind': 'bot', 'network_id': 2,
                         'local': False, 'state': {'health': 500}}
        battle._records = {'bot:1': source_record, 'bot:2': target_record}
        battle._destructibles = types.SimpleNamespace(
            shot_world_distance=mock.Mock(return_value={
                'world_distance': 9.8, 'piercing_loss': 0.0,
                'stop_distance': 9.8, 'continue_from': None,
                'stopped_by_destructible': False}))
        battle.client = types.SimpleNamespace(
            send_bot_bot_hit=mock.Mock(), send_bot_human_hit=mock.Mock())
        state = {
            'id': 1,
            'target_kind': 'bot', 'target_id': 2,
            'shell_index': 0,
            'shot_yaw': 0.0, 'shot_pitch': 0.0,
        }

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'combat_rules.sample_penetration_factor') as draw:
            battle._resolve_bot_shot(state, 1)

        draw.assert_not_called()
        battle.client.send_bot_bot_hit.assert_not_called()

    def test_ap_disappears_when_obstacles_exhaust_nominal_piercing(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        shot = _Descriptor().gun.shots[0]
        shot.piercingPower = (40.0, 40.0)
        responses = [
            {'world_distance': 999999.0, 'piercing_loss': 25.0,
             'stop_distance': None, 'continue_from': 2.0},
            {'world_distance': 999999.0, 'piercing_loss': 25.0,
             'stop_distance': None, 'continue_from': 2.0},
        ]
        battle._destructibles = types.SimpleNamespace(
            shot_world_distance=mock.Mock(side_effect=responses))

        result = battle._resolve_shot_scene(
            _Vector(), _Vector(0, 0, 10), _Vector(0, 0, 1), shot)

        self.assertEqual(4.0, result['world_distance'])
        self.assertEqual(50.0, result['piercing_loss'])
        self.assertTrue(result['stopped_by_destructible'])
        self.assertEqual(
            2, battle._destructibles.shot_world_distance.call_count)

    def test_one_low_roll_exhausts_30_mm_after_two_obstacles(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        shot = _Descriptor().gun.shots[0]
        shot.piercingPower = (40.0, 40.0)
        responses = [
            {'world_distance': 999999.0, 'piercing_loss': 25.0,
             'stop_distance': None, 'continue_from': 2.0},
            {'world_distance': 999999.0, 'piercing_loss': 25.0,
             'stop_distance': None, 'continue_from': 2.0},
        ]
        battle._destructibles = types.SimpleNamespace(
            shot_world_distance=mock.Mock(side_effect=responses))

        result = battle._resolve_shot_scene(
            _Vector(), _Vector(0, 0, 10), _Vector(0, 0, 1), shot,
            penetration_factor=0.75)

        self.assertEqual(4.0, result['world_distance'])
        self.assertEqual(50.0, result['piercing_loss'])
        self.assertTrue(result['stopped_by_destructible'])

    def test_one_high_roll_keeps_24_mm_after_one_obstacle(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        shot = _Descriptor().gun.shots[0]
        shot.piercingPower = (39.2, 39.2)
        responses = [
            {'world_distance': 999999.0, 'piercing_loss': 25.0,
             'stop_distance': None, 'continue_from': 2.0},
            {'world_distance': 999999.0, 'piercing_loss': 0.0,
             'stop_distance': None, 'continue_from': None},
        ]
        battle._destructibles = types.SimpleNamespace(
            shot_world_distance=mock.Mock(side_effect=responses))

        result = battle._resolve_shot_scene(
            _Vector(), _Vector(0, 0, 10), _Vector(0, 0, 1), shot,
            penetration_factor=1.25)

        self.assertAlmostEqual(
            24.0, combat_rules.sampled_piercing(
                shot, 2.0, 1.25, result['piercing_loss']))
        self.assertEqual(999999.0, result['world_distance'])
        self.assertFalse(result['stopped_by_destructible'])

    def test_thick_obstacle_uses_entry_distance_for_range_loss(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        shot = _Descriptor().gun.shots[0]
        shot.piercingPower = (30.0, 20.0)
        shot.maxDistance = 200.0
        responses = [
            {'world_distance': 999999.0, 'piercing_loss': 25.0,
             'loss_distance': 100.0, 'stop_distance': None,
             'continue_from': 150.0},
            {'world_distance': 999999.0, 'piercing_loss': 0.0,
             'stop_distance': None, 'continue_from': None},
        ]
        battle._destructibles = types.SimpleNamespace(
            shot_world_distance=mock.Mock(side_effect=responses))

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'combat_rules.sample_penetration_factor',
                return_value=1.0) as draw:
            result = battle._resolve_shot_scene(
                _Vector(), _Vector(0, 0, 200), _Vector(0, 0, 1), shot)

        draw.assert_called_once_with()
        self.assertEqual(999999.0, result['world_distance'])
        self.assertEqual(25.0, result['piercing_loss'])
        self.assertEqual(1.0, result['penetration_factor'])
        self.assertFalse(result['stopped_by_destructible'])

    def test_he_and_heat_stop_at_first_destructible(self):
        for kind in ('HIGH_EXPLOSIVE', 'HOLLOW_CHARGE'):
            runtime = _runtime()
            battle = BattleRuntime(runtime)
            battle._avatar = runtime.bigworld.avatar
            descriptor = _Descriptor()
            descriptor.gun.shots[0].shell.kind = kind
            battle._destructibles = types.SimpleNamespace(
                shot_world_distance=mock.Mock(return_value={
                    'world_distance': 3.0, 'piercing_loss': 0.0,
                    'stop_distance': 3.0, 'continue_from': None,
                    'stopped_by_destructible': True}))

            with mock.patch(
                    'gui.mods.offline_lan_0922.battle_runtime.'
                    'combat_rules.sample_penetration_factor') as draw:
                result = battle._resolve_shot_scene(
                    _Vector(), _Vector(0, 0, 5), _Vector(0, 0, 1),
                    descriptor.gun.shots[0])

            draw.assert_not_called()
            self.assertEqual(3.0, result['world_distance'])
            self.assertEqual(0.0, result['piercing_loss'])
            self.assertIsNone(result['penetration_factor'])
            self.assertTrue(result['stopped_by_destructible'])
            self.assertEqual(
                1, battle._destructibles.shot_world_distance.call_count)

    def test_he_destructible_bursts_before_vehicle_but_heat_does_not(self):
        for kind, splash_count in (('HIGH_EXPLOSIVE', 1),
                                   ('HOLLOW_CHARGE', 0)):
            runtime = _runtime()
            battle = BattleRuntime(runtime)
            battle._avatar = runtime.bigworld.avatar
            descriptor = _Descriptor()
            descriptor.gun.shots[0].shell.kind = kind
            source = _Vehicle(10, descriptor, _Vector(), (0, 0, 0),
                              {'health': 500})
            target = _Vehicle(11, _Descriptor(), _Vector(0, 0, 5),
                              (0, 0, 0), {'health': 500})
            target.collideSegmentExt = lambda start, end: [
                types.SimpleNamespace(
                    dist=5.0, hitAngleCos=1.0,
                    matInfo=types.SimpleNamespace(armor=10.0),
                    compName='vehicleHull')]
            runtime.bigworld.entities.update({10: source, 11: target})
            battle._server = types.SimpleNamespace(vehicle_id=10)
            battle._records = {
                'player:1': {'engine_id': 10, 'kind': 'player',
                             'network_id': 1, 'local': True},
                'bot:2': {'engine_id': 11, 'state': {'health': 500},
                          'kind': 'bot', 'network_id': 2, 'local': False}}
            runtime.bigworld.avatar.gunRotator = types.SimpleNamespace(
                getCurShotPosition=lambda: (
                    _Vector(), _Vector(0, 0, 1)))
            battle._destructibles = types.SimpleNamespace(
                shot_world_distance=mock.Mock(return_value={
                    'world_distance': 3.0, 'piercing_loss': 0.0,
                    'stop_distance': 3.0, 'continue_from': None,
                    'stopped_by_destructible': True}))
            battle.client = types.SimpleNamespace(
                player_id=1, send_bot_hit=mock.Mock())
            battle._he_splash = mock.Mock()

            battle._resolve_hit(7, 0.0, 0.0)

            self.assertEqual(0, battle.client.send_bot_hit.call_count)
            self.assertEqual(splash_count, battle._he_splash.call_count)
            if splash_count:
                self.assertAlmostEqual(3.0,
                                       battle._he_splash.call_args.args[0].z)

    def test_vehicle_before_destructible_never_mutates_prop(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        calls = []

        def sensor(unused_bigworld, unused_space, start, end,
                   unused_direction, unused_shot):
            calls.append((start, end))
            if (end - start).length > 5.0:
                self.fail('vehicle cap exposed the prop behind it')
            return {'world_distance': 999999.0, 'piercing_loss': 0.0,
                    'stop_distance': None, 'continue_from': None}

        battle._destructibles = types.SimpleNamespace(
            shot_world_distance=sensor)
        result = battle._resolve_shot_scene(
            _Vector(), _Vector(0, 0, 5), _Vector(0, 0, 1),
            _Descriptor().gun.shots[0])

        self.assertEqual(1, len(calls))
        self.assertEqual(999999.0, result['world_distance'])

    def test_he_splash_uses_vehicle_ray_and_skips_direct_target(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        descriptor = _Descriptor()
        shell = descriptor.gun.shots[0].shell
        shell.kind = 'HIGH_EXPLOSIVE'
        shell.damage = (400.0,)
        shell.explosionRadius = 10.0
        source = _Vehicle(10, descriptor, _Vector(100, 0, 100),
                          (0, 0, 0), {'health': 500})
        direct = _Vehicle(11, _Descriptor(), _Vector(1, 0, 0),
                          (0, 0, 0), {'health': 500})
        splash = _Vehicle(12, _Descriptor(), _Vector(5, 0, 0),
                          (0, 0, 0), {'health': 500})
        far = _Vehicle(13, _Descriptor(), _Vector(20, 0, 0),
                       (0, 0, 0), {'health': 500})
        material = types.SimpleNamespace(
            armor=20.0, vehicleDamageFactor=1.0,
            chanceToHitByExplosion=1.0)
        splash.collideSegmentExt = lambda start, end: [types.SimpleNamespace(
            dist=1.0, hitAngleCos=1.0, matInfo=material,
            compName='vehicleHull')]
        runtime.bigworld.entities.update({
            10: source, 11: direct, 12: splash, 13: far})
        direct_record = {
            'engine_id': 11, 'state': {'health': 500}, 'kind': 'bot',
            'network_id': 1, 'local': False}
        battle._records = {
            'player:1': {
                'engine_id': 10, 'state': {'health': 500}, 'kind': 'player',
                'network_id': 1, 'local': True},
            'bot:1': direct_record,
            'bot:2': {
                'engine_id': 12,
                'state': {'health': 500, 'combat_base_revision': 7,
                          'combat_ack_seq': 3}, 'kind': 'bot',
                'network_id': 2, 'local': False},
            'bot:3': {
                'engine_id': 13, 'state': {'health': 500}, 'kind': 'bot',
                'network_id': 3, 'local': False},
        }
        battle.client = types.SimpleNamespace(
            send_bot_hit=mock.Mock(return_value=True))

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'critical_damage.propose_explosion',
                side_effect=lambda *args, **kwargs: (
                    args[4] + 111, {'events': []}, {
                        'devices': [], 'crew_ko': [], 'ignite': False,
                    })) as apply_critical:
            count = battle._he_splash(
                _Vector(0, 0, 0), descriptor.gun.shots[0], 7,
                direct_record, 'player', 1, 10)

        self.assertEqual(1, count)
        sent = battle.client.send_bot_hit.call_args
        self.assertEqual(2, sent.args[0])
        self.assertEqual(
            sent.kwargs['hull_damage'] + 111, sent.args[2])
        self.assertTrue(sent.kwargs['splash'])
        self.assertGreater(sent.kwargs['hull_damage'], 0)
        self.assertEqual(7, sent.kwargs[
            'critical_target_base_revision'])
        self.assertEqual(3, sent.kwargs['critical_target_ack_seq'])
        self.assertFalse(apply_critical.call_args.kwargs['deadeye'])
        self.assertGreater(apply_critical.call_args.args[3].length, 0.0)

    def test_health_transition_calls_native_vehicle_death_path(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        record = {'engine_id': 10, 'local': False}

        battle._apply_health(record, {'health': 0})

        self.assertEqual((0, 0, 0), entity.health_change)

    def test_local_death_crosses_stock_postmortem_activation_boundary(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        battle._avatar.playerVehicleID = 10
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        record = {
            'engine_id': 10,
            'state': {'team': 1, 'health': 500, 'alive': True},
            'kind': 'player', 'network_id': 1, 'local': True}
        battle._records = {'player:1': record}
        handler = battle._avatar.inputHandler
        handler.activatePostmortem = mock.Mock()
        original_update = battle._avatar.updateVehicleHealth

        def exact_update(vehicle_id, health, death_reason_id,
                         is_crew_active, is_respawn):
            original_update(vehicle_id, health, death_reason_id,
                            is_crew_active, is_respawn)
            # Exact #1513 PlayerAvatar.updateVehicleHealth performs this
            # synchronous call on the alive -> dead transition.
            if health <= 0 or not is_crew_active:
                handler.activatePostmortem(is_respawn)

        battle._avatar.updateVehicleHealth = exact_update
        battle._last_health[10] = (500, 500, True, 0)

        with mock.patch.object(
                battle_runtime_module.critical_damage, 'apply_death',
                return_value=None):
            battle._apply_health(record, {
                'health': 0, 'display_health': 0, 'alive': False,
                'death_reason': 0}, attacker_id=11, reason_id=0,
                force_cause=True)

        self.assertEqual((10, 0, 0, False, False),
                         battle._avatar.health_update)
        handler.activatePostmortem.assert_called_once_with(False)
        battle._binding.arena_vehicle_killed.assert_called_once_with(
            10, 11, 0)

    def test_terminal_critical_state_does_not_replay_device_hits_to_flash(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        record = {
            'engine_id': 10,
            'state': {'team': 1, 'health': 500, 'alive': True},
            'kind': 'player', 'network_id': 1, 'local': True}
        battle._records = {'player:1': record}
        battle._last_health[10] = (500, 500, True, 0)
        terminal = {
            'devices': [], 'destroyed': ['engineHealth'],
            'crew_ko': ['driver'], 'fire': False,
            'ammo_rack_death': False,
            'events': [
                {'kind': 'device', 'name': 'engineHealth',
                 'state': 'destroyed', 'cause': 'shot'},
                {'kind': 'crew', 'name': 'driver',
                 'state': 'destroyed', 'cause': 'shot'},
            ]}
        battle._present_critical = mock.Mock(return_value=True)
        battle._sync_fire_effect = mock.Mock(return_value=True)

        with mock.patch.object(
                critical_damage, 'apply_death', return_value=terminal):
            battle._apply_health(record, {
                'health': 0, 'display_health': 0, 'alive': False,
                'death_reason': 0}, attacker_id=11, reason_id=0,
                force_cause=True)

        battle._present_critical.assert_not_called()
        battle._sync_fire_effect.assert_called_once_with(entity)
        self.assertEqual([], record['critical_state']['events'])
        self.assertIsNone(battle._local_damage_report)

    def test_critical_proposal_carries_exact_descriptor_crew_roster(self):
        descriptor = _Descriptor()
        descriptor.type = types.SimpleNamespace(crewRoles=(
            ('commander', 'radioman'), ('driver',), ('gunner',),
            ('gunner', 'loader'), ('loader',), ('radioman',)))
        target = _Vehicle(
            11, descriptor, _Vector(0, 0, 1), (0, 0, 0),
            {'health': 500})
        critical = {'crew_ko': ['commander'], 'events': []}

        result = BattleRuntime._critical_with_crew_roster(target, critical)

        self.assertEqual([
            'commander', 'driver', 'gunner1', 'gunner2', 'loader1',
            'radioman1'], result['crew_roster'])
        self.assertNotIn('crew_roster', critical)
        descriptor.type.crewRoles = ()
        self.assertIs(
            critical,
            BattleRuntime._critical_with_crew_roster(target, critical))

    def test_whole_crew_knockout_kills_with_remaining_hull_health(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        battle._avatar.playerVehicleID = 10
        entity = _Vehicle(
            11, _Descriptor(), _Vector(0, 0, 1), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities[11] = entity
        record = {
            'engine_id': 11, 'state': {'team': 2, 'health': 500},
            'kind': 'bot', 'network_id': 2, 'local': False,
            'presentation': True}
        battle._records = {'bot:2': record}

        with mock.patch.object(
                critical_damage, 'apply_death') as apply_death:
            battle._apply_health(record, {
                'health': 500, 'display_health': 500, 'alive': False,
                'death_reason': 0}, attacker_id=10, reason_id=0,
                force_cause=True)

        self.assertEqual(500, entity.health)
        self.assertFalse(entity.isCrewActive)
        apply_death.assert_not_called()
        battle._binding.arena_vehicle_killed.assert_called_once_with(
            11, 10, 0)
        feedback = battle._avatar.guiSessionProvider.shared.feedback
        feedback.setVehicleState.assert_called_once_with(
            11, runtime.feedback_event_id.VEHICLE_DEAD, False)

    def test_ram_death_preserves_ramming_critical_cause(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        record = {'engine_id': 10, 'local': False}

        with mock.patch.object(
                critical_damage, 'apply_death', return_value=None) as death:
            battle._apply_health(record, {'health': 0}, reason_id=2)

        death.assert_called_once_with(entity, 'ramming')

    def test_combat_event_separates_attack_and_death_reason(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        attacker = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                            {'health': 500})
        target = _Vehicle(11, _Descriptor(), _Vector(0, 0, 1), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities.update({10: attacker, 11: target})
        battle._records = {
            'player:1': {
                'engine_id': 10,
                'state': {'health': 500, 'team': 1},
                'kind': 'player', 'network_id': 1, 'local': True},
            'bot:2': {
                'engine_id': 11,
                'state': {'health': 500, 'team': 2},
                'kind': 'bot', 'network_id': 2, 'local': False,
                'presentation': True},
        }
        battle._avatar.playerVehicleID = 10
        battle._synchronise_player_identity(10)
        presentation_order = []
        battle._binding.arena_vehicle_killed.side_effect = (
            lambda *args: presentation_order.append(('killed', args)))
        present_health = battle._avatar.guiSessionProvider.setVehicleHealth
        present_health.side_effect = (
            lambda *args: presentation_order.append(('health', args)))

        self.assertTrue(battle._apply_combat_event({
            'kind': 'bot_hit', 'attacker': 1, 'target_bot': 2,
            'health': 0, 'dead': True, 'attack_reason': 0,
            'death_reason': 3, 'source': 'shot',
            'world_pose': True, 'x': 0.0, 'y': 0.0, 'z': 1.0,
            'shell_index': 0, 'shot_result': 2, 'damage': 500}))

        self.assertEqual((0, 10, 0), target.health_change)
        present_health.assert_called_once_with(False, 11, 0, 10, 0)
        battle._binding.arena_vehicle_killed.assert_called_once_with(
            11, 10, 3)
        self.assertEqual([
            ('health', (False, 11, 0, 10, 0)),
            ('killed', (11, 10, 3)),
        ], presentation_order)

    def test_local_kill_cause_survives_native_crew_deactivation(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._binding = mock.Mock()
        battle._avatar.playerVehicleID = 10
        target = _Vehicle(
            11, _Descriptor(), _Vector(0, 0, 1), (0, 0, 0),
            {'health': 500})
        runtime.bigworld.entities[11] = target
        record = {
            'engine_id': 11,
            'state': {'health': 500, 'alive': True, 'team': 2},
            'kind': 'bot', 'network_id': 2, 'local': False,
            'presentation': True, 'native_remote': True,
        }
        battle._records = {'bot:2': record}
        battle._last_health[11] = (500, 500, True, 0)
        presentation_order = []
        present_health = battle._avatar.guiSessionProvider.setVehicleHealth
        present_health.side_effect = lambda *args: presentation_order.append(
            ('health', args))
        battle._binding.arena_vehicle_killed.side_effect = (
            lambda *args: presentation_order.append(('killed', args)))

        def native_health_changed(health, attacker_id, reason_id):
            target.health_change = (health, attacker_id, reason_id)
            present_health(
                False, target.id, health, attacker_id, reason_id)

        def native_crew_changed(previous):
            target.previous_crew_active = previous
            # Exact #1513 Vehicle.set_isCrewActive calls this feedback path
            # with the default attacker and attack reason.
            present_health(False, target.id, target.health, 0, 0)

        target.onHealthChanged = native_health_changed
        target.set_isCrewActive = native_crew_changed
        battle._fallback_postmortem_viewpoint = mock.Mock()

        with mock.patch.object(
                critical_damage, 'apply_death', return_value=None):
            battle._apply_health(
                record, {
                    'health': 0, 'display_health': 0, 'alive': False,
                    'death_reason': 0,
                }, attacker_id=10, reason_id=0, force_cause=True)

        self.assertEqual([
            mock.call(False, 11, 0, 10, 0),
            mock.call(False, 11, 0, 0, 0),
            mock.call(False, 11, 0, 10, 0),
        ], present_health.call_args_list)
        self.assertEqual([
            ('health', (False, 11, 0, 10, 0)),
            ('health', (False, 11, 0, 0, 0)),
            ('health', (False, 11, 0, 10, 0)),
            ('killed', (11, 10, 0)),
        ], presentation_order)

    def test_server_owned_frag_and_team_killer_updates_use_native_arena(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._binding = mock.Mock()
        battle._records = {
            'player:1': {
                'engine_id': 10, 'state': {'frags': 0},
                'kind': 'player', 'network_id': 1, 'local': True},
        }

        event = {
            'kind': 'vehicle_statistics', 'actor_kind': 'player',
            'actor_id': 1, 'frags': -1, 'team_killer': True}
        self.assertTrue(battle._apply_vehicle_statistics_event(event))
        battle._binding.arena_vehicle_statistics.assert_called_once_with(
            10, -1)
        battle._binding.arena_team_killer.assert_called_once_with(10)

        self.assertFalse(battle._apply_vehicle_statistics_event(event))
        battle._binding.arena_vehicle_statistics.assert_called_once_with(
            10, -1)
        battle._binding.arena_team_killer.assert_called_once_with(10)

    def test_death_snapshot_resolves_durable_attacker_before_health(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._server = types.SimpleNamespace()
        battle._binding = mock.Mock()
        battle._binding.is_vehicle_ready.return_value = True
        attacker = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                            {'health': 500})
        victim = _Vehicle(11, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities.update({10: attacker, 11: victim})
        battle._records = {
            'player:1': {
                'engine_id': 10, 'state': {'health': 500}, 'ready': True,
                'kind': 'player', 'network_id': 1, 'local': True},
            'bot:2': {
                'engine_id': 11,
                'state': {
                    'health': 0, 'alive': False, 'death_reason': 3,
                    'death_attacker_kind': 'player',
                    'death_attacker_id': 1},
                'ready': True, 'kind': 'bot', 'network_id': 2,
                'local': False},
        }

        self.assertTrue(battle._materialize_record(battle._records['bot:2']))

        self.assertEqual((0, 10, 3), victim.health_change)
        battle._binding.arena_vehicle_killed.assert_called_once_with(
            11, 10, 3)

    def test_stop_restores_account_after_native_callback_boundary(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._map_create_attempted = True
        native_owner = object()
        battle._local_model = native_owner
        calls = []
        runtime.offline_map_creator.destroy = lambda: calls.append('destroy')
        runtime.compatibility.restore_lobby_account = (
            lambda: calls.append('restore'))
        type(runtime.app_loader).lobby_callback = lambda: calls.append(
            'lobby')

        battle.stop(show_login=False)

        self.assertEqual(['destroy'], calls)
        self.assertEqual('stopped', battle.state)
        self.assertIn(native_owner, battle._retired_native_owners)
        runtime.bigworld.callbacks.pop()()
        self.assertEqual(['destroy', 'restore'], calls)
        self.assertEqual([], battle._retired_native_owners)

    def test_cleanup_leaves_vehicle_teardown_to_native_avatar_then_map(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._map_create_attempted = True
        binding = mock.Mock()
        server = mock.Mock()
        battle._binding = binding
        battle._server = server
        battle._records = {
            'player:1': {'engine_id': 10, 'local': True},
            'bot:2': {'engine_id': 11, 'local': False}}
        runtime.bigworld.entities[11] = _Vehicle(
            11, _Descriptor(), _Vector(), (0, 0, 0), {'health': 500})
        calls = []

        def retire():
            calls.append('retire')

        def destroy():
            calls.append('destroy')
            runtime.bigworld.clearEntitiesAndSpaces()

        runtime.compatibility.retire_current_player = retire
        runtime.offline_map_creator.destroy = destroy

        battle._cleanup()

        self.assertEqual(['retire', 'destroy'], calls)
        binding.destroy_entity.assert_not_called()
        binding.arena_vehicle_removed.assert_not_called()
        server.destroy.assert_not_called()
        self.assertIsNone(battle._binding)

    def test_cleanup_destroys_remote_presentations_before_native_space(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._map_create_attempted = True
        calls = []

        class _FailingRemoteFactory(object):
            def engine_active(self):
                return True

            def destroy_all(self):
                calls.append('remote')
                raise RuntimeError('remote cleanup failed')

        battle._remote_factory = _FailingRemoteFactory()

        def retire():
            calls.append('retire')

        def destroy():
            calls.append('destroy')
            runtime.bigworld.avatar = None

        runtime.compatibility.retire_current_player = retire
        runtime.offline_map_creator.destroy = destroy

        with self.assertRaisesRegex(RuntimeError, 'remote cleanup failed'):
            battle._cleanup()

        self.assertEqual(['remote', 'retire', 'destroy'], calls)
        self.assertIsNone(battle._remote_factory)

    def test_cleanup_after_an_engine_reset_leaves_the_outline_alone(self):
        """abandon_visual exists because the engine already freed these
        objects, and the edge-detect removal reads the same entity."""
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._map_create_attempted = True

        class _AbandonedRemoteFactory(object):
            def engine_active(self):
                return False

            def get(self, entity_id):
                raise AssertionError('read a freed presentation')

            def destroy_all(self):
                return True

        battle._remote_factory = _AbandonedRemoteFactory()
        battle._outlined_engine_id = 11
        battle._records = {
            'bot:2': {'engine_id': 11, 'local': False,
                      'presentation': True, 'visual_started': True}}

        battle._cleanup()

        self.assertEqual([], runtime.bigworld.edge_removes)
        self.assertIsNone(battle._outlined_engine_id)

    def test_cleanup_releases_space_id_lost_by_stock_destroy_failure(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._map_create_attempted = True
        creator = runtime.offline_map_creator
        creator._OfflineMapCreator__spaceId = 7
        creator._OfflineMapCreator__spaceMappingId = 3
        client_spaces = set([7])
        calls = []

        runtime.compatibility.retire_current_player = (
            lambda: calls.append(('retire',)))
        runtime.bigworld.isClientSpace = (
            lambda space_id: space_id in client_spaces)
        runtime.bigworld.delSpaceGeometryMapping = (
            lambda space_id, mapping_id:
            calls.append(('mapping', space_id, mapping_id)))
        runtime.bigworld.clearSpace = (
            lambda space_id: calls.append(('clear', space_id)))

        def release(space_id):
            calls.append(('release', space_id))
            client_spaces.discard(space_id)

        runtime.bigworld.releaseSpace = release

        def lossy_destroy():
            calls.append(('destroy',))
            runtime.bigworld.avatar = None
            creator._OfflineMapCreator__spaceId = 0
            creator._OfflineMapCreator__spaceMappingId = 0

        creator.destroy = lossy_destroy

        battle._cleanup()

        self.assertEqual([
            ('retire',), ('destroy',), ('mapping', 7, 3),
            ('clear', 7), ('release', 7)], calls)
        self.assertNotIn(7, client_spaces)

    def test_lan_disconnect_still_restores_fake_account(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._map_create_attempted = True
        calls = []
        runtime.offline_map_creator.destroy = lambda: calls.append('destroy')
        runtime.compatibility.restore_lobby_account = (
            lambda: calls.append('restore'))

        battle.stop(show_login=True)

        self.assertEqual(['destroy'], calls)
        self.assertEqual('stopped', battle.state)
        runtime.bigworld.callbacks.pop()()
        self.assertEqual(['destroy', 'restore'], calls)

    def test_global_shutdown_cleans_battle_without_recreating_account(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._map_create_attempted = True
        calls = []
        runtime.offline_map_creator.destroy = lambda: calls.append('destroy')
        runtime.compatibility.restore_lobby_account = (
            lambda: calls.append('restore'))

        battle.stop(show_login=False, restore_account=False)

        self.assertEqual(['destroy'], calls)
        self.assertEqual('stopped', battle.state)

    def test_failed_deferred_account_restore_disconnects_cleanly(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._map_create_attempted = True
        runtime.compatibility.restore_lobby_account = mock.Mock(
            side_effect=RuntimeError('restore failed'))

        battle.stop()
        runtime.bigworld.callbacks.pop()()

        self.assertEqual('stopped', battle.state)
        self.assertEqual('lobby restore failed: restore failed', battle.error)
        self.assertEqual(1, runtime.compatibility.disconnect_calls)
        self.assertIsNone(battle._avatar)
        self.assertIsNone(battle._server)
        battle.stop()

    def test_dirty_stock_teardown_never_restores_account_over_zombie_avatar(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._map_create_attempted = True
        runtime.offline_map_creator.destroy = lambda: None
        runtime.bigworld.clearEntitiesAndSpaces = lambda: None
        runtime.bigworld.clearAllSpaces = lambda: None
        runtime.compatibility.restore_lobby_account = mock.Mock()

        with self.assertRaisesRegex(RuntimeError,
                                    'retained the Avatar'):
            battle.stop()

        self.assertEqual('stopped', battle.state)
        runtime.compatibility.restore_lobby_account.assert_not_called()

    def test_rejected_map_attempt_runs_full_destroy_before_account_restore(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        calls = []
        original_clear = runtime.bigworld.clearEntitiesAndSpaces

        def clear_lobby():
            calls.append('clear')
            original_clear()

        def partial_create(unused_map_name):
            runtime.bigworld.avatar = object()
            runtime.offline_map_creator.active = False

        def full_destroy():
            calls.append('destroy')
            runtime.bigworld.avatar = None

        def restore():
            self.assertIsNone(runtime.bigworld.avatar)
            calls.append('restore')

        runtime.offline_map_creator.create = partial_create
        runtime.offline_map_creator.destroy = full_destroy
        runtime.compatibility.restore_lobby_account = restore
        runtime.bigworld.clearEntitiesAndSpaces = clear_lobby

        self.assertFalse(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, _minimal_start(), _Client()))

        self.assertEqual(['clear', 'destroy'], calls)
        runtime.bigworld.callbacks.pop()()
        self.assertEqual(['clear', 'destroy', 'restore'], calls)
        self.assertEqual('failed', battle.state)
        self.assertFalse(battle._map_create_attempted)

    def test_partial_avatar_is_rejected_and_fully_destroyed(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        calls = []
        original_clear = runtime.bigworld.clearEntitiesAndSpaces

        def clear_lobby():
            calls.append('clear')
            original_clear()

        def create_partial(unused_map_name):
            runtime.offline_map_creator.active = True
            runtime.bigworld.avatar = object()

        def destroy_partial():
            calls.append('destroy')
            runtime.offline_map_creator.active = False
            runtime.bigworld.avatar = None

        runtime.offline_map_creator.create = create_partial
        runtime.offline_map_creator.destroy = destroy_partial
        runtime.compatibility.restore_lobby_account = (
            lambda: calls.append('restore'))
        runtime.bigworld.clearEntitiesAndSpaces = clear_lobby

        self.assertFalse(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, _minimal_start(), _Client()))

        self.assertEqual(['clear', 'destroy'], calls)
        runtime.bigworld.callbacks.pop()()
        self.assertEqual(['clear', 'destroy', 'restore'], calls)
        self.assertEqual('failed', battle.state)

    def test_avatar_leave_defers_destroy_until_mailbox_returns(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()
        start = {
            'round_id': 1, 'map': '01_karelia', 'bot_authority_id': 1,
            'players': [{
                'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                'vehicle': 'ussr:R11_MS-1', 'health': 500}],
            'bots': []}
        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, start, client))
        runtime.bigworld.callbacks.pop(0)()
        runtime.bigworld.callbacks.pop(0)()
        self.assertEqual('running', battle.state)
        server = battle._server

        server.leaveArena({})

        self.assertEqual('leaving', battle.state)
        self.assertIs(server, battle._server)
        self.assertIsNone(battle._remote_factory)
        self.assertIsNone(battle._callback_id)
        self.assertIsNone(battle._ammo_callback_id)
        previous_snapshot = battle._last_snapshot
        battle.on_snapshot({'round_id': 1, 'server_tick': 999})
        self.assertIs(previous_snapshot, battle._last_snapshot)
        self.assertFalse(battle.on_events({'events': []}))
        runtime.bigworld.callbacks.pop()()
        self.assertEqual('stopped', battle.state)
        self.assertIsNone(battle._server)

    def test_avatar_leave_delegates_session_ownership_after_mailbox_returns(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()
        local_leave = mock.Mock()
        start = {
            'round_id': 1, 'map': '01_karelia', 'bot_authority_id': 1,
            'players': [{
                'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                'vehicle': 'ussr:R11_MS-1', 'health': 500}],
            'bots': []}
        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, start, client,
            on_local_leave=local_leave))
        runtime.bigworld.callbacks.pop(0)()
        runtime.bigworld.callbacks.pop(0)()
        server = battle._server

        server.leaveArena({})

        local_leave.assert_not_called()
        runtime.bigworld.callbacks.pop()()
        local_leave.assert_called_once_with()
        self.assertEqual('leaving', battle.state)
        self.assertIs(server, battle._server)

    def test_avatar_leave_still_defers_after_presentation_cleanup_failure(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()
        start = {
            'round_id': 1, 'map': '01_karelia', 'bot_authority_id': 1,
            'players': [{
                'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                'vehicle': 'ussr:R11_MS-1', 'health': 500}],
            'bots': []}
        self.assertTrue(battle.start({
            'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
            'name': 'Player'}, start, client))
        runtime.bigworld.callbacks.pop(0)()
        runtime.bigworld.callbacks.pop(0)()
        server = battle._server
        battle._quiesce_native_presentations = mock.Mock(
            side_effect=RuntimeError('presentation cleanup failed'))

        with mock.patch('sys.stdout'):
            self.assertTrue(server.leaveArena({}))

        self.assertEqual('leaving', battle.state)
        self.assertIs(server, battle._server)
        self.assertTrue(runtime.bigworld.callbacks)

    def test_same_runtime_can_cleanly_create_a_second_round(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()

        for round_id in (1, 2):
            start = {
                'round_id': round_id, 'map': '01_karelia',
                'bot_authority_id': 1,
                'players': [{
                    'id': 1, 'team': 1, 'slot': 0, 'name': 'Player',
                    'vehicle': 'ussr:R11_MS-1', 'health': 500}],
                'bots': []}
            self.assertTrue(battle.start({
                'map': '01_karelia', 'vehicle': 'ussr:R11_MS-1',
                'name': 'Player'}, start, client))
            runtime.bigworld.callbacks.pop(0)()
            runtime.bigworld.callbacks.pop(0)()
            self.assertEqual('running', battle.state)
            self.assertEqual(round_id, battle._sync.round_id)
            self.assertFalse(battle._round_finished_notified)
            if round_id == 1:
                battle.stop(show_login=False)
                self.assertEqual('stopped', battle.state)
                runtime.bigworld.callbacks.pop()()
                runtime.bigworld.callbacks[:] = []

    def test_async_failure_recovers_lobby_and_notifies_session(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'loading_entities'
        battle._map_create_attempted = True
        calls = []
        runtime.offline_map_creator.destroy = lambda: calls.append('destroy')
        runtime.compatibility.restore_lobby_account = (
            lambda: calls.append('restore'))
        type(runtime.app_loader).lobby_callback = lambda: calls.append(
            'lobby')
        callback = mock.Mock()
        battle.client = types.SimpleNamespace(on_event=callback)

        battle._fail(RuntimeError('entity loading failed'))

        self.assertEqual(['destroy'], calls)
        self.assertEqual('failed', battle.state)
        callback.assert_not_called()
        runtime.bigworld.callbacks.pop()()
        self.assertEqual(['destroy', 'restore'], calls)
        callback.assert_called_once_with(
            'battle_failed', {
                'message': 'entity loading failed',
                'round_id': None,
                'lobby_restored': True,
            })

    def test_failure_retains_native_owner_until_deferred_lobby_restore(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        owner = object()
        battle._avatar = owner
        battle._cleanup = lambda: setattr(battle, '_avatar', None)
        restore = mock.Mock()
        runtime.compatibility.restore_lobby_account = restore
        callback = mock.Mock()
        battle.client = types.SimpleNamespace(on_event=callback)

        battle._fail(RuntimeError('entity loading failed'))

        restore.assert_not_called()
        callback.assert_not_called()
        self.assertIn(owner, battle._retired_native_owners)
        runtime.bigworld.callbacks.pop()()
        restore.assert_called_once_with()
        callback.assert_called_once_with('battle_failed', {
            'message': 'entity loading failed',
            'round_id': None,
            'lobby_restored': True,
        })
        self.assertEqual([], battle._retired_native_owners)

    def test_global_stop_cancels_pending_failure_lobby_restore(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        cleanup = mock.Mock()
        battle._cleanup = cleanup
        restore = mock.Mock()
        runtime.compatibility.restore_lobby_account = restore
        callback = mock.Mock()
        battle.client = types.SimpleNamespace(on_event=callback)

        battle._fail(RuntimeError('entity loading failed'))
        self.assertTrue(battle.lobby_restore_pending())

        battle.stop(restore_account=False)

        self.assertEqual('stopped', battle.state)
        self.assertFalse(battle.lobby_restore_pending())
        cleanup.assert_called_once_with()
        runtime.bigworld.callbacks.pop()()
        restore.assert_not_called()
        callback.assert_not_called()

    def test_failed_lobby_restore_is_reported_without_transport_error(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'loading_entities'
        battle._start_message = {'round_id': 9}
        battle._map_create_attempted = True
        runtime.offline_map_creator.destroy = lambda: None
        runtime.bigworld.avatar = None
        runtime.compatibility.restore_lobby_account = mock.Mock(
            side_effect=RuntimeError('replacement Account failed'))
        callback = mock.Mock()
        battle.client = types.SimpleNamespace(on_event=callback)

        battle._fail(RuntimeError('entity loading failed'))

        self.assertEqual('failed', battle.state)
        self.assertIn('entity loading failed', battle.error)
        callback.assert_not_called()
        runtime.bigworld.callbacks.pop()()
        self.assertIn('replacement Account failed', battle.error)
        self.assertEqual(1, runtime.compatibility.disconnect_calls)
        self.assertIsNone(runtime.bigworld.player())
        callback.assert_called_once_with('battle_failed', {
            'message': battle.error,
            'round_id': 9,
            'lobby_restored': False,
        })

    def test_retirement_failure_does_not_skip_map_destroy_or_disconnect(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'loading_entities'
        battle._start_message = {'round_id': 11}
        battle._map_create_attempted = True
        runtime.compatibility.retire_current_player = mock.Mock(
            side_effect=RuntimeError('native retirement failed'))
        runtime.offline_map_creator.destroy = mock.Mock(
            side_effect=runtime.bigworld.clearEntitiesAndSpaces)
        callback = mock.Mock()
        battle.client = types.SimpleNamespace(on_event=callback)

        battle._fail(RuntimeError('entity loading failed'))

        runtime.offline_map_creator.destroy.assert_called_once_with()
        self.assertIsNone(runtime.bigworld.player())
        self.assertEqual(1, runtime.compatibility.disconnect_calls)
        self.assertIn('native retirement failed', battle.error)
        callback.assert_called_once_with('battle_failed', {
            'message': battle.error,
            'round_id': 11,
            'lobby_restored': False,
        })

    def test_force_clear_runs_after_native_retirement_failure(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        runtime.compatibility.retire_current_player = mock.Mock(
            side_effect=RuntimeError('native retirement failed'))

        error = battle._force_clear_engine_player(
            'engine retained its player')

        self.assertIsNone(runtime.bigworld.player())
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual('native retirement failed', str(error))
        self.assertIn(('clear_entities_spaces',), runtime.bigworld.operations)

    def test_failure_notification_exception_never_replaces_first_error(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle.state = 'loading_entities'
        battle._start_message = {'round_id': 9}
        battle._map_create_attempted = True
        runtime.offline_map_creator.destroy = lambda: None
        runtime.bigworld.avatar = None
        runtime.compatibility.restore_lobby_account = lambda: object()

        def fail_callback(kind, message):
            raise RuntimeError('notification failed')

        battle.client = types.SimpleNamespace(on_event=fail_callback)

        battle._fail(RuntimeError('first native failure'))

        runtime.bigworld.callbacks.pop()()
        self.assertEqual('failed', battle.state)
        self.assertEqual('first native failure', battle.error)

    def test_bot_to_bot_collision_uses_authority_report(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        source = _Vehicle(10, _Descriptor(), _Vector(0, 0, 0), (0, 0, 0),
                          {'health': 500})
        target = _Vehicle(11, _Descriptor(), _Vector(0, 0, 20), (0, 0, 0),
                          {'health': 500})
        collision = types.SimpleNamespace(
            dist=20.0, hitAngleCos=1.0,
            matInfo=types.SimpleNamespace(armor=10.0),
            compName='vehicleHull')
        target.collideSegmentExt = lambda start, end: [collision]
        runtime.bigworld.entities.update({10: source, 11: target})
        battle._records = {
            'bot:1': {'engine_id': 10, 'state': {'team': 1},
                      'kind': 'bot', 'network_id': 1},
            'bot:2': {'engine_id': 11,
                      'state': {'team': 2, 'combat_base_revision': 6,
                                'combat_ack_seq': 2},
                      'kind': 'bot', 'network_id': 2}}
        battle.client = types.SimpleNamespace(
            send_bot_bot_hit=mock.Mock(return_value=True))
        battle._shell_damage = mock.Mock(return_value=(80, 2))
        battle._critical_hit = lambda *args, **kwargs: (
            400, {'events': []},
            {'devices': [], 'crew_ko': [], 'ignite': False})

        self.assertTrue(battle._resolve_bot_shot({
            'id': 1, 'target_kind': 'bot', 'target_id': 2,
            'shell_index': 0}, 3))
        battle.client.send_bot_bot_hit.assert_called_once()
        sent = battle.client.send_bot_bot_hit.call_args
        self.assertEqual(400, sent.args[3])
        self.assertEqual(80, sent.kwargs['hull_damage'])
        self.assertEqual(6, sent.kwargs[
            'critical_target_base_revision'])
        self.assertEqual(2, sent.kwargs['critical_target_ack_seq'])

    def test_bot_shot_uses_same_destructible_loss_and_vehicle_cap(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        source = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        target = _Vehicle(11, _Descriptor(), _Vector(0, 0, 5), (0, 0, 0),
                          {'health': 500})
        target.collideSegmentExt = lambda start, end: [types.SimpleNamespace(
            dist=5.0, hitAngleCos=1.0,
            matInfo=types.SimpleNamespace(armor=10.0),
            compName='vehicleHull')]
        runtime.bigworld.entities.update({10: source, 11: target})
        battle._records = {
            'bot:1': {'engine_id': 10, 'state': {'team': 1},
                      'kind': 'bot', 'network_id': 1},
            'bot:2': {'engine_id': 11,
                      'state': {'team': 2, 'combat_base_revision': 6,
                                'combat_ack_seq': 2},
                      'kind': 'bot', 'network_id': 2}}
        battle.client = types.SimpleNamespace(
            send_bot_bot_hit=mock.Mock(return_value=True))
        battle._destructibles = types.SimpleNamespace(
            shot_world_distance=mock.Mock(return_value={
                'world_distance': 999999.0, 'piercing_loss': 25.0,
                'stop_distance': None, 'continue_from': None}))
        battle._shell_damage = mock.Mock(return_value=(80, 2))
        battle._critical_hit = lambda *args, **kwargs: (
            80, {'events': []},
            {'devices': [], 'crew_ko': [], 'ignite': False})

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'combat_rules.sample_penetration_factor',
                return_value=0.8) as draw:
            self.assertTrue(battle._resolve_bot_shot({
                'id': 1, 'target_kind': 'bot', 'target_id': 2,
                'shell_index': 0}, 3))

        draw.assert_called_once_with()
        ray = battle._destructibles.shot_world_distance.call_args.args
        self.assertAlmostEqual(5.0, (ray[3] - ray[2]).length)
        self.assertEqual(
            25.0, battle._shell_damage.call_args.kwargs['pierce_loss'])
        factor = battle._shell_damage.call_args.kwargs[
            'penetration_factor']
        self.assertEqual(0.8, factor)

    def test_player_reuses_one_penetration_factor_for_scene_and_vehicle(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        source = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        target = _Vehicle(11, _Descriptor(), _Vector(0, 0, 5),
                          (0, 0, 0), {'health': 500})
        target.collideSegmentExt = lambda start, end: [types.SimpleNamespace(
            dist=5.0, hitAngleCos=1.0,
            matInfo=types.SimpleNamespace(armor=10.0),
            compName='vehicleHull')]
        runtime.bigworld.entities.update({10: source, 11: target})
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._records = {
            'player:1': {'engine_id': 10, 'kind': 'player',
                         'network_id': 1, 'local': True},
            'bot:2': {'engine_id': 11,
                      'state': {'combat_base_revision': 7,
                                'combat_ack_seq': 3}, 'kind': 'bot',
                      'network_id': 2, 'local': False}}
        runtime.bigworld.avatar.gunRotator = types.SimpleNamespace(
            getCurShotPosition=lambda: (
                _Vector(), _Vector(0, 0, 1)))
        battle.client = types.SimpleNamespace(
            player_id=1, send_bot_hit=mock.Mock(return_value=True))
        battle._critical_hit = lambda *args, **kwargs: (
            args[5], {'events': []},
            {'devices': [], 'crew_ko': [], 'ignite': False})
        battle._destructibles = types.SimpleNamespace(
            shot_world_distance=mock.Mock(side_effect=(
                {'world_distance': 999999.0, 'piercing_loss': 25.0,
                 'loss_distance': 2.0, 'stop_distance': None,
                 'continue_from': 2.1},
                {'world_distance': 999999.0, 'piercing_loss': 0.0,
                 'stop_distance': None, 'continue_from': None})))
        observed = {}

        def damage(descriptor, collisions, distance, shell_index=None,
                   pierce_loss=0.0, penetration_factor=None,
                   target_descriptor=None):
            observed['vehicle'] = penetration_factor
            return 120, 2

        battle._shell_damage = damage
        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'combat_rules.sample_penetration_factor',
                return_value=0.75) as draw:
            battle._resolve_hit(7, 0.0, 0.0)

        draw.assert_called_once_with()
        self.assertEqual(0.75, observed['vehicle'])

    def test_direct_vehicle_draws_penetration_only_when_hit_is_resolved(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        source = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        target = _Vehicle(11, _Descriptor(), _Vector(0, 0, 5),
                          (0, 0, 0), {'health': 500})
        target.collideSegmentExt = lambda start, end: [types.SimpleNamespace(
            dist=5.0, hitAngleCos=1.0,
            matInfo=types.SimpleNamespace(armor=10.0),
            compName='vehicleHull')]
        runtime.bigworld.entities.update({10: source, 11: target})
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._records = {
            'player:1': {'engine_id': 10, 'kind': 'player',
                         'network_id': 1, 'local': True},
            'bot:2': {'engine_id': 11,
                      'state': {'combat_base_revision': 7,
                                'combat_ack_seq': 3}, 'kind': 'bot',
                      'network_id': 2, 'local': False}}
        runtime.bigworld.avatar.gunRotator = types.SimpleNamespace(
            getCurShotPosition=lambda: (_Vector(), _Vector(0, 0, 1)))
        battle.client = types.SimpleNamespace(
            player_id=1, send_bot_hit=mock.Mock(return_value=True))
        battle._destructibles = types.SimpleNamespace(
            shot_world_distance=mock.Mock(return_value={
                'world_distance': 999999.0, 'piercing_loss': 0.0,
                'stop_distance': None, 'continue_from': None}))
        battle._shell_damage = mock.Mock(return_value=(120, 2))
        battle._critical_hit = lambda *args, **kwargs: (
            args[5], {'events': []},
            {'devices': [], 'crew_ko': [], 'ignite': False})

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'combat_rules.sample_penetration_factor',
                return_value=1.1) as draw:
            battle._resolve_hit(7, 0.0, 0.0)

        draw.assert_called_once_with()
        self.assertEqual(
            1.1, battle._shell_damage.call_args.kwargs[
                'penetration_factor'])

    def test_pure_player_miss_does_not_draw_penetration_factor(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        source = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = source
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._records = {
            'player:1': {'engine_id': 10, 'kind': 'player',
                         'network_id': 1, 'local': True}}
        runtime.bigworld.avatar.gunRotator = types.SimpleNamespace(
            getCurShotPosition=lambda: (_Vector(), _Vector(0, 0, 1)))
        battle.client = types.SimpleNamespace(player_id=1)
        battle._destructibles = types.SimpleNamespace(
            shot_world_distance=mock.Mock(return_value={
                'world_distance': 999999.0, 'piercing_loss': 0.0,
                'stop_distance': None, 'continue_from': None}))

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'combat_rules.sample_penetration_factor') as draw:
            battle._resolve_hit(7, 0.0, 0.0)

        draw.assert_not_called()

    def test_bot_shot_resolver_uses_dispersed_barrel_ray(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        source = _Vehicle(10, _Descriptor(), _Vector(0, 0, 0), (0, 0, 0),
                          {'health': 500})
        target = _Vehicle(11, _Descriptor(), _Vector(0, 0, 20), (0, 0, 0),
                          {'health': 500})
        segments = []
        collision = types.SimpleNamespace(
            dist=20.0, hitAngleCos=1.0,
            matInfo=types.SimpleNamespace(armor=10.0),
            compName='vehicleHull')

        def collide(start, end):
            segments.append((start, end))
            return [collision]

        target.collideSegmentExt = collide
        runtime.bigworld.entities.update({10: source, 11: target})
        battle._records = {
            'bot:1': {'engine_id': 10, 'state': {'team': 1},
                      'kind': 'bot', 'network_id': 1},
            'bot:2': {'engine_id': 11, 'state': {'team': 2},
                      'kind': 'bot', 'network_id': 2}}
        battle.client = types.SimpleNamespace(
            send_bot_bot_hit=mock.Mock(return_value=True))
        battle._critical_hit = lambda *args, **kwargs: (
            args[5], None, None)

        self.assertTrue(battle._resolve_bot_shot({
            'id': 1, 'target_kind': 'bot', 'target_id': 2,
            'shell_index': 0, 'shot_yaw': math.pi / 2.0,
            'shot_pitch': 0.1}, 3))

        start, end = segments[0]
        direction = end - start
        self.assertAlmostEqual(500.0, direction.length, places=4)
        direction.normalise()
        self.assertAlmostEqual(math.cos(0.1), direction.x, places=5)
        self.assertAlmostEqual(math.sin(0.1), direction.y, places=5)
        self.assertAlmostEqual(0.0, direction.z, places=5)

    def test_bot_shot_resolver_reports_damage_to_local_human(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        source = _Vehicle(10, _Descriptor(), _Vector(0, 0, 0), (0, 0, 0),
                          {'health': 500})
        target = _Vehicle(11, _Descriptor(), _Vector(0, 0, 20), (0, 0, 0),
                          {'health': 500})
        collision = types.SimpleNamespace(
            dist=20.0, hitAngleCos=1.0,
            matInfo=types.SimpleNamespace(
                armor=10.0, vehicleDamageFactor=1.0),
            compName='vehicleHull')
        target.collideSegmentExt = mock.Mock(side_effect=AssertionError(
            'native collision uses the stale retail vehicle filter'))
        runtime.bigworld.entities.update({10: source, 11: target})
        battle._records = {
            'bot:1': {'engine_id': 10, 'state': {'team': 2},
                      'kind': 'bot', 'network_id': 1},
            'player:7': {'engine_id': 11,
                         'state': {'team': 1,
                                   'critical_base_revision': 4,
                                   'critical_ack_seq': 1},
                         'kind': 'player', 'network_id': 7, 'local': True}}
        battle._local_matrix = _Matrix(target.matrix)
        battle.client = types.SimpleNamespace(
            send_bot_human_hit=mock.Mock(return_value=True))
        battle._shell_damage = mock.Mock(return_value=(90, 2))
        battle._critical_hit = lambda *args, **kwargs: (
            450, {'events': []},
            {'devices': [], 'crew_ko': [], 'ignite': False})

        with mock.patch(
                'gui.mods.offline_lan_0922.battle_runtime.'
                'collide_vehicle_at_matrix',
                return_value=[collision]) as collide_at_matrix:
            self.assertTrue(battle._resolve_bot_shot({
                'id': 1, 'target_kind': 'human', 'target_id': 7,
                'shell_index': 0, 'shot_yaw': 0.0,
                'shot_pitch': 0.0}, 3))

        args = battle.client.send_bot_human_hit.call_args[0]
        self.assertEqual((1, 7, 3), args[:3])
        self.assertEqual(450, args[3])
        kwargs = battle.client.send_bot_human_hit.call_args.kwargs
        self.assertEqual(90, kwargs['hull_damage'])
        self.assertEqual(4, kwargs['critical_target_base_revision'])
        self.assertEqual(1, kwargs['critical_target_ack_seq'])
        collide_at_matrix.assert_called_once()
        self.assertIs(target, collide_at_matrix.call_args[0][0])
        self.assertIs(battle._local_matrix,
                      collide_at_matrix.call_args[0][1])
        target.collideSegmentExt.assert_not_called()

    def test_bot_shot_collision_contract_failure_is_not_a_silent_miss(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        source = _Vehicle(10, _Descriptor(), _Vector(0, 0, 0), (0, 0, 0),
                          {'health': 500})
        target = _Vehicle(11, _Descriptor(), _Vector(0, 0, 20), (0, 0, 0),
                          {'health': 500})
        target.collideSegmentExt = mock.Mock(
            side_effect=RuntimeError('native collision failed'))
        runtime.bigworld.entities.update({10: source, 11: target})
        battle._records = {
            'bot:1': {'engine_id': 10, 'state': {'team': 2},
                      'kind': 'bot', 'network_id': 1},
            'player:7': {'engine_id': 11, 'state': {'team': 1},
                         'kind': 'player', 'network_id': 7}}

        with self.assertRaisesRegex(RuntimeError, 'native collision failed'):
            battle._resolve_bot_shot({
                'id': 1, 'target_kind': 'human', 'target_id': 7,
                'shell_index': 0, 'shot_yaw': 0.0,
                'shot_pitch': 0.0}, 3)

    def test_bot_ram_message_uses_authority_client_contract(self):
        battle = BattleRuntime(_runtime())
        battle.client = types.SimpleNamespace(
            send_bot_ram=mock.Mock(return_value=True))
        battle._bots = types.SimpleNamespace(
            ack_human_ram_receipt=mock.Mock())

        self.assertTrue(battle._send_bot_message({
            'type': 'bot_ram', 'bot_id': 11, 'target_kind': 'human',
            'target_id': 2, 'ram_seq': 4, 'damage_to_bot': 20,
            'damage_to_target': 40}))

        battle.client.send_bot_ram.assert_called_once_with(
            11, 'human', 2, 4, 20, 40, None, None)

        battle.client.send_bot_ram.reset_mock()
        self.assertTrue(battle._send_bot_message({
            'type': 'bot_ram', 'bot_id': 11, 'target_kind': 'human',
            'target_id': 2, 'ram_seq': 5, 'damage_to_bot': 21,
            'damage_to_target': 41,
            'ram_contact_player_id': 2, 'ram_contact_seq': 9}))
        battle.client.send_bot_ram.assert_called_once_with(
            11, 'human', 2, 5, 21, 41, 2, 9)
        battle._bots.ack_human_ram_receipt.assert_not_called()

    def test_bot_state_uses_the_already_projected_client_boundary(self):
        battle = BattleRuntime(_runtime())
        battle.client = types.SimpleNamespace(
            send_projected_bot_state=mock.Mock(return_value=True),
            send_bot_state=mock.Mock(return_value=False))
        bots = [{'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0,
                 'yaw': 0.0, 'health': 1000, 'alive': True,
                 'fire_seq': 0}]

        self.assertTrue(battle._send_bot_message({
            'type': 'bot_state', 'bots': bots}))

        battle.client.send_projected_bot_state.assert_called_once_with(bots)
        battle.client.send_bot_state.assert_not_called()

        battle.client.send_projected_bot_state.reset_mock()
        self.assertTrue(battle._send_bot_message({
            'type': 'bot_state', 'bots': bots,
            'sample_time_us': 40000,
            'source_batch_horizon_us': 40000}))
        battle.client.send_projected_bot_state.assert_called_once_with(
            bots, sample_time_us=40000,
            source_batch_horizon_us=40000)

    @staticmethod
    def _pending_manifest_outbox(payload, round_id=7, authority_id=-1):
        outbox = bot_runtime.BotRuntime(authority_id)
        outbox.round_id = round_id
        outbox.authority_id = authority_id
        outbox._pending_manifest = copy.deepcopy(payload)
        outbox._pending_manifest_round_id = round_id
        outbox._pending_manifest_authority_id = authority_id
        return outbox

    def test_bot_manifest_retries_same_payload_until_enqueue(self):
        payload = {
            'type': 'bot_manifest',
            'bots': [{'id': 11, 'name': 'Frozen'}],
            'player_collision_profiles': [{
                'id': 1, 'vehicle': 'ussr:R11_MS-1',
                'mass': 25000.0, 'shape': [1.5, 3.5, -0.8, 2.0],
                'ram_profile': {
                    'spall_coefficient': 1.0,
                    'ramming_bonus': 0.0}}],
        }
        battle = BattleRuntime(_runtime())
        battle._config = {'startupTimeoutSeconds': 0.6}
        battle.state = 'running'
        battle._worker_mode = True
        battle._start_message = {'round_id': 7}
        battle._bots = self._pending_manifest_outbox(payload)
        battle.client = types.SimpleNamespace(
            round_id=7, authority_epoch=3,
            is_bot_authority=lambda: True,
            send_bot_manifest=mock.Mock(
                side_effect=[False, False, True]))

        self.assertFalse(battle._enqueue_bot_manifest(payload, now=1.0))
        self.assertEqual(1.6, battle._bot_manifest_retry_deadline)
        self.assertFalse(battle._enqueue_bot_manifest(payload, now=1.1))
        self.assertFalse(battle._retry_bot_manifest(1.25))
        self.assertEqual(1.6, battle._bot_manifest_retry_deadline)
        self.assertFalse(battle._retry_bot_manifest(1.49))
        self.assertTrue(battle._retry_bot_manifest(1.5))
        self.assertFalse(battle._retry_bot_manifest(2.0))

        self.assertEqual(3, battle.client.send_bot_manifest.call_count)
        expected = (
            payload['bots'], payload['player_collision_profiles'])
        self.assertTrue(all(
            call.args == expected
            for call in battle.client.send_bot_manifest.call_args_list))
        self.assertTrue(battle._bots._manifest_sent)
        self.assertIsNone(battle._bots.pending_manifest())
        self.assertEqual(0.0, battle._next_bot_manifest_retry)
        self.assertEqual(0.0, battle._bot_manifest_retry_deadline)

    def test_bot_manifest_retry_timeout_enters_worker_failure_lifecycle(self):
        runtime = _runtime()
        runtime.bigworld.now = 1.5
        payload = {'type': 'bot_manifest', 'bots': []}
        callback = mock.Mock()
        battle = BattleRuntime(runtime)
        battle._config = {'startupTimeoutSeconds': 0.5}
        battle.state = 'running'
        battle._worker_mode = True
        battle._battle_live = False
        battle._prebattle_deadline = None
        battle._last_frame_time = 1.4
        battle._frame_diagnostics = None
        battle._start_message = {'round_id': 7}
        outbox = self._pending_manifest_outbox(payload)
        battle._bots = outbox
        battle.client = types.SimpleNamespace(
            round_id=7, authority_epoch=3,
            is_bot_authority=lambda: True,
            send_bot_manifest=mock.Mock(return_value=False),
            on_event=callback)
        battle._flush_pending_bot_create = mock.Mock()
        battle._flush_pending_entities = mock.Mock()
        battle._drain_event_journal = mock.Mock()
        battle._maybe_send_battle_ready = mock.Mock()

        self.assertFalse(battle._enqueue_bot_manifest(payload, now=1.0))
        self.assertFalse(battle._retry_bot_manifest(1.25))
        self.assertEqual(1.5, battle._bot_manifest_retry_deadline)

        with mock.patch('sys.stdout'):
            battle._frame()

        self.assertEqual('failed', battle.state)
        self.assertEqual(
            'worker bot manifest enqueue timed out', battle.error)
        self.assertIsNone(outbox.pending_manifest())
        self.assertEqual(2, battle.client.send_bot_manifest.call_count)
        callback.assert_not_called()
        self.assertEqual(1, len(runtime.bigworld.callbacks))

        with mock.patch('sys.stdout'):
            runtime.bigworld.callbacks.pop(0)()

        callback.assert_called_once_with('battle_failed', {
            'message': 'worker bot manifest enqueue timed out',
            'round_id': 7,
            'lobby_restored': True,
        })

    def test_bot_manifest_normal_first_enqueue_does_not_retry(self):
        payload = {'type': 'bot_manifest', 'bots': []}
        battle = BattleRuntime(_runtime())
        battle.state = 'running'
        battle._worker_mode = True
        battle._start_message = {'round_id': 7}
        battle._bots = self._pending_manifest_outbox(payload)
        battle.client = types.SimpleNamespace(
            round_id=7, authority_epoch=3,
            is_bot_authority=lambda: True,
            send_bot_manifest=mock.Mock(return_value=True))

        self.assertTrue(battle._enqueue_bot_manifest(payload, now=1.0))
        self.assertFalse(battle._retry_bot_manifest(2.0))

        battle.client.send_bot_manifest.assert_called_once_with([])
        self.assertTrue(battle._bots._manifest_sent)

    def test_worker_prebattle_frame_retries_manifest_without_bot_state(self):
        runtime = _runtime()
        runtime.bigworld.now = 1.0
        payload = {'type': 'bot_manifest', 'bots': []}
        battle = BattleRuntime(runtime)
        battle.state = 'running'
        battle._worker_mode = True
        battle._battle_live = False
        battle._prebattle_deadline = None
        battle._last_frame_time = 0.9
        battle._frame_diagnostics = None
        battle._start_message = {'round_id': 7}
        battle._bots = self._pending_manifest_outbox(payload)
        battle._bots.update = mock.Mock(return_value=[])
        battle.client = types.SimpleNamespace(
            round_id=7, authority_epoch=3,
            is_bot_authority=lambda: True,
            send_bot_manifest=mock.Mock(return_value=True))
        battle._flush_pending_bot_create = mock.Mock()
        battle._flush_pending_entities = mock.Mock()
        battle._drain_event_journal = mock.Mock()
        battle._maybe_send_battle_ready = mock.Mock()
        battle._schedule = mock.Mock()

        battle._frame()

        battle.client.send_bot_manifest.assert_called_once_with([])
        battle._bots.update.assert_not_called()
        battle._schedule.assert_called_once_with(0.0, battle._frame)

    def test_bot_manifest_retry_is_fenced_by_lifecycle_change(self):
        mutations = (
            ('generation', lambda battle: setattr(
                battle, '_generation', battle._generation + 1)),
            ('authority', lambda battle: setattr(
                battle._bots, 'authority_id', 2)),
            ('round', lambda battle: battle._start_message.update(
                round_id=8)),
        )
        for name, mutate in mutations:
            with self.subTest(name=name):
                payload = {'type': 'bot_manifest', 'bots': []}
                battle = BattleRuntime(_runtime())
                battle.state = 'running'
                battle._worker_mode = True
                battle._start_message = {'round_id': 7}
                battle._bots = self._pending_manifest_outbox(payload)
                battle.client = types.SimpleNamespace(
                    round_id=7, authority_epoch=3,
                    is_bot_authority=lambda: True,
                    send_bot_manifest=mock.Mock(return_value=False))
                self.assertFalse(
                    battle._enqueue_bot_manifest(payload, now=1.0))

                mutate(battle)

                self.assertFalse(battle._retry_bot_manifest(2.0))
                self.assertEqual(
                    1, battle.client.send_bot_manifest.call_count)
                self.assertIsNone(battle._bots.pending_manifest())
                self.assertEqual(0.0, battle._next_bot_manifest_retry)
                self.assertEqual(
                    0.0, battle._bot_manifest_retry_deadline)

    def test_bot_launch_outbox_survives_bot_state_enqueue_failure(self):
        battle = BattleRuntime(_runtime())
        outbox = bot_runtime.BotRuntime(1)
        launch = {'id': 11, 'fire_seq': 1, 'shot_yaw': 0.25}
        self.assertTrue(outbox._queue_pending_launch(launch))
        launch['shot_yaw'] = 1.0
        battle._bots = outbox
        outbox.authority_id = 1
        battle._send_bot_message = mock.Mock(side_effect=[False, True])
        battle._launch_bot_projectile = mock.Mock(return_value=True)
        publication = {
            'type': 'bot_state', 'bots': [],
            'launches': [dict(outbox._pending_launches[0])],
        }

        self.assertFalse(battle._enqueue_bot_message(publication))
        self.assertEqual([1], [
            value['fire_seq'] for value in outbox._pending_launches])
        self.assertEqual(0.25, outbox._pending_launches[0]['shot_yaw'])
        self.assertEqual({}, battle._bot_fire_seen)
        battle._launch_bot_projectile.assert_not_called()

        self.assertTrue(battle._enqueue_bot_message(publication))
        self.assertEqual([1], [
            value['fire_seq'] for value in outbox._pending_launches])
        self.assertEqual({}, battle._bot_fire_seen)
        self.assertTrue(battle._confirm_bot_projectile_launch({
            'shooter_kind': 'bot', 'shooter_id': 11, 'shot_seq': 1,
        }))
        self.assertEqual([], outbox._pending_launches)
        self.assertEqual({11: 1}, battle._bot_fire_seen)

    def test_bot_launch_outbox_retries_from_first_failed_launch(self):
        battle = BattleRuntime(_runtime())
        outbox = bot_runtime.BotRuntime(1)
        for fire_seq in (1, 2, 3):
            self.assertTrue(outbox._queue_pending_launch({
                'id': 11, 'fire_seq': fire_seq,
            }))
        battle._bots = outbox
        outbox.authority_id = 1
        battle._send_bot_message = mock.Mock(return_value=True)
        attempts = []

        def enqueue_launch(unused_state, fire_seq):
            attempts.append(fire_seq)
            return fire_seq != 2 or attempts.count(2) > 1

        battle._launch_bot_projectile = mock.Mock(
            side_effect=enqueue_launch)
        first = {
            'type': 'bot_state', 'bots': [],
            'launches': [dict(value)
                         for value in outbox._pending_launches],
        }

        self.assertTrue(battle._enqueue_bot_message(first))
        self.assertEqual([1, 2], attempts)
        self.assertEqual([1, 2, 3], [
            value['fire_seq'] for value in outbox._pending_launches])
        self.assertEqual({}, battle._bot_fire_seen)

        retry = {
            'type': 'bot_state', 'bots': [],
            'launches': [dict(value)
                         for value in outbox._pending_launches],
        }
        self.assertTrue(battle._enqueue_bot_message(retry))
        self.assertEqual([1, 2, 1, 2, 3], attempts)
        self.assertEqual([1, 2, 3], [
            value['fire_seq'] for value in outbox._pending_launches])
        self.assertTrue(battle._confirm_bot_projectile_launch({
            'shooter_kind': 'bot', 'shooter_id': 11, 'shot_seq': 3,
        }))
        self.assertEqual({}, battle._bot_fire_seen)
        self.assertTrue(battle._confirm_bot_projectile_launch({
            'shooter_kind': 'bot', 'shooter_id': 11, 'shot_seq': 1,
        }))
        self.assertEqual({11: 1}, battle._bot_fire_seen)
        self.assertTrue(battle._confirm_bot_projectile_launch({
            'shooter_kind': 'bot', 'shooter_id': 11, 'shot_seq': 2,
        }))
        self.assertEqual([], outbox._pending_launches)
        self.assertEqual({11: 3}, battle._bot_fire_seen)

        self.assertTrue(battle._resolve_bot_fire(first))
        self.assertEqual([1, 2, 1, 2, 3], attempts)

    def test_bot_launch_failure_blocks_only_that_shooters_tail(self):
        battle = BattleRuntime(_runtime())
        outbox = bot_runtime.BotRuntime(1)
        for bot_id, fire_seq in ((11, 1), (11, 2), (12, 1)):
            self.assertTrue(outbox._queue_pending_launch({
                'id': bot_id, 'fire_seq': fire_seq,
            }))
        battle._bots = outbox
        outbox.authority_id = 1
        battle._send_bot_message = mock.Mock(return_value=True)
        attempts = []

        def enqueue_launch(state, fire_seq):
            key = (state['id'], fire_seq)
            attempts.append(key)
            return key != (11, 1) or attempts.count(key) > 1

        battle._launch_bot_projectile = mock.Mock(
            side_effect=enqueue_launch)
        first = {
            'type': 'bot_state', 'bots': [],
            'launches': [dict(value)
                         for value in outbox._pending_launches],
        }

        self.assertTrue(battle._enqueue_bot_message(first))
        self.assertEqual([(11, 1), (12, 1)], attempts)
        self.assertEqual([(11, 1), (11, 2), (12, 1)], [
            (value['id'], value['fire_seq'])
            for value in outbox._pending_launches])
        self.assertEqual({}, battle._bot_fire_seen)

        retry = {
            'type': 'bot_state', 'bots': [],
            'launches': [dict(value)
                         for value in outbox._pending_launches],
        }
        self.assertTrue(battle._enqueue_bot_message(retry))
        self.assertEqual(
            [(11, 1), (12, 1), (11, 1), (11, 2), (12, 1)],
            attempts)
        self.assertTrue(battle._confirm_bot_projectile_launch({
            'shooter_kind': 'bot', 'shooter_id': 12, 'shot_seq': 1,
        }))
        self.assertEqual([(11, 1), (11, 2)], [
            (value['id'], value['fire_seq'])
            for value in outbox._pending_launches])
        self.assertTrue(battle._confirm_bot_projectile_launch({
            'shooter_kind': 'bot', 'shooter_id': 11, 'shot_seq': 1,
        }))
        self.assertTrue(battle._confirm_bot_projectile_launch({
            'shooter_kind': 'bot', 'shooter_id': 11, 'shot_seq': 2,
        }))
        self.assertEqual([], outbox._pending_launches)
        self.assertEqual({11: 2, 12: 1}, battle._bot_fire_seen)

    def test_malformed_frozen_bot_launch_fails_the_worker_boundary(self):
        battle = BattleRuntime(_runtime())
        battle._bots = bot_runtime.BotRuntime(1)
        battle._send_bot_message = mock.Mock(return_value=True)
        battle._launch_bot_projectile = mock.Mock()

        with self.assertRaisesRegex(
                RuntimeError, 'outbox identity is invalid'):
            battle._enqueue_bot_message({
                'type': 'bot_state', 'bots': [],
                'launches': [{'id': 11}],
            })

        battle._launch_bot_projectile.assert_not_called()

    def test_snapshot_health_is_forwarded_to_authority_runtime(self):
        battle = BattleRuntime(_runtime())
        battle._bots = types.SimpleNamespace(apply_snapshot=mock.Mock())
        battle._sync = types.SimpleNamespace(snapshot=mock.Mock())
        snapshot = {'server_tick': 8, 'bots': [
            {'id': 2, 'health': 0, 'alive': False}]}

        battle.on_snapshot(snapshot)

        battle._bots.apply_snapshot.assert_called_once_with(snapshot)
        battle._sync.snapshot.assert_called_once_with(snapshot)

    def test_dead_player_and_terminal_battle_cannot_keep_driving(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        client = _Client()
        battle.client = client
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(0, 0, 0), (0, 0, 0),
                          {'health': 0})
        runtime.bigworld.entities[10] = entity
        battle._server = types.SimpleNamespace(vehicle_id=10)
        battle._sender = types.SimpleNamespace(
            forward=1.0, turn=0.0,
            send_current=lambda: client.send_input('current'))

        battle._drive_local(0.1)
        self.assertEqual([], entity.teleports)

        entity.health = 500
        battle._battle_result = {'winner': 1}
        battle._drive_local(0.1)
        self.assertEqual([], entity.teleports)

    def test_visible_authority_loss_never_primes_fire_sequences(self):
        battle = BattleRuntime(_runtime())
        battle._start_message = {'round_id': 4, 'bot_authority_id': -1}
        battle._last_snapshot = {'bots': [
            {'id': 11, 'fire_seq': 9, 'health': 500, 'alive': True}]}
        battle._bots = types.SimpleNamespace(
            authority_id=-1,
            battle_start=mock.Mock(return_value=[]),
            apply_snapshot=mock.Mock(), is_authority=lambda: False)

        battle.on_events({'events': [{
            'event_id': '4:1:0',
            'kind': 'authority', 'round_id': 4, 'player_id': None}]})

        self.assertEqual({}, battle._bot_fire_seen)
        battle._bots.battle_start.assert_called_once_with({
            'round_id': 4, 'bot_authority_id': None})
        battle._bots.apply_snapshot.assert_called_once_with(
            battle._last_snapshot)

    def test_loading_roster_updates_authority_before_bots_exist(self):
        battle = BattleRuntime(_runtime())
        battle.state = 'loading'
        battle._start_message = {
            'round_id': 4, 'bot_authority_id': 2,
            'bots': [{'id': 11, 'team': 1, 'slot': 0}]}

        self.assertTrue(battle.on_roster({
            'round_id': 4, 'phase': 'loading',
            'bot_authority_id': 1}))

        self.assertEqual(1, battle._start_message['bot_authority_id'])

    def test_visible_snapshot_tracks_infrastructure_without_takeover(self):
        battle = BattleRuntime(_runtime())
        battle._start_message = {'round_id': 4, 'bot_authority_id': -1}
        battle._send_bot_message = mock.Mock(return_value=True)
        bots = types.SimpleNamespace(
            authority_id=-1,
            battle_start=mock.Mock(return_value=[]),
            apply_snapshot=mock.Mock(), is_authority=lambda: False)
        battle._bots = bots
        snapshot = {
            'round_id': 4, 'bot_authority_id': 0,
            'bot_manifest': [{
                'id': 11,
                'profile': {'dominant_role': 'sniper'},
                'route': {'id': 'ridge', 'waypoints': []}}],
            'bots': [{'id': 11, 'fire_seq': 9,
                      'health': 500, 'alive': True,
                      'x': 123.0, 'y': 4.0, 'z': -87.0, 'yaw': 1.25}]}

        battle.on_snapshot(snapshot)

        bots.battle_start.assert_called_once_with({
            'round_id': 4, 'bot_authority_id': 0})
        battle._send_bot_message.assert_not_called()
        bots.apply_snapshot.assert_called_once_with(snapshot)
        self.assertEqual({}, battle._bot_fire_seen)


    def test_he_that_only_reaches_a_track_still_blasts_the_hull(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        shell = types.SimpleNamespace(
            compactDescr=1, damage=(900.0,), caliber=152.0,
            kind='HIGH_EXPLOSIVE', explosionRadius=3.0, effectsIndex=1)
        shot = types.SimpleNamespace(
            shell=shell, piercingPower=(60.0, 60.0), speed=500.0,
            gravity=9.81, maxDistance=700.0)
        source = types.SimpleNamespace(
            gun=types.SimpleNamespace(shots=[shot]), activeGunShotIndex=0)
        track = types.SimpleNamespace(armor=20.0, vehicleDamageFactor=0.0)
        collisions = (types.SimpleNamespace(
            dist=5.0, hitAngleCos=0.2, matInfo=track,
            compName='vehicleChassis'),)
        target = types.SimpleNamespace(
            hull=types.SimpleNamespace(materials={
                'front': types.SimpleNamespace(
                    armor=45.0, vehicleDamageFactor=1.0)}))

        damage, result = battle._shell_damage(
            source, collisions, 200.0, target_descriptor=target)

        self.assertEqual(1, result)
        self.assertGreater(damage, 0)

    def test_solid_shot_that_only_reaches_a_track_deals_no_hull_damage(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        shell = types.SimpleNamespace(
            compactDescr=1, damage=(400.0,), caliber=88.0,
            kind='ARMOR_PIERCING', effectsIndex=1)
        shot = types.SimpleNamespace(
            shell=shell, piercingPower=(150.0, 130.0), speed=800.0,
            gravity=9.81, maxDistance=700.0)
        source = types.SimpleNamespace(
            gun=types.SimpleNamespace(shots=[shot]), activeGunShotIndex=0)
        track = types.SimpleNamespace(armor=20.0, vehicleDamageFactor=0.0)
        collisions = (types.SimpleNamespace(
            dist=5.0, hitAngleCos=0.2, matInfo=track,
            compName='vehicleChassis'),)
        target = types.SimpleNamespace(
            hull=types.SimpleNamespace(materials={
                'front': types.SimpleNamespace(
                    armor=45.0, vehicleDamageFactor=1.0)}))

        damage, result = battle._shell_damage(
            source, collisions, 200.0, target_descriptor=target)

        self.assertEqual((0, 1), (damage, result))

    def test_local_track_feed_publishes_engine_mode_and_belt_speeds(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        entity = _Vehicle(
            10, _Descriptor(), _Vector(), (0, 0, 0), {'health': 500})
        battle._sender = types.SimpleNamespace(forward=1.0, turn=0.0)
        battle._local_physics = vehicle_physics.derive_params(
            entity.typeDescriptor)
        battle._local_speed = 5.0
        battle._local_turn_speed = 0.0

        battle._update_local_tracks(entity)

        self.assertEqual((2, 1), entity.engineMode)
        self.assertEqual([(2, 1)], entity.engine_modes)
        self.assertEqual([(5.0, 5.0)], entity.track_scrolls)

    def test_local_track_feed_turns_the_engine_off_on_death(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        entity = _Vehicle(
            10, _Descriptor(), _Vector(), (0, 0, 0), {'health': 0})
        battle._sender = types.SimpleNamespace(forward=0.0, turn=0.0)
        battle._local_physics = vehicle_physics.derive_params(
            entity.typeDescriptor)

        battle._update_local_tracks(entity)

        self.assertEqual([(0, 0)], entity.engine_modes)
        self.assertEqual([], entity.track_scrolls)

    def test_local_track_failure_keeps_live_pose_presentation_running(self):
        runtime = _runtime()
        runtime.compatibility.set_vehicle_pose_overlay = mock.Mock()
        battle = BattleRuntime(runtime)
        battle._local_matrix = _Matrix()
        battle._local_model = object()
        battle._local_camera_velocity = _Vector()
        battle._local_position = (3.0, 4.0, 5.0)
        battle._local_yaw = 0.25
        battle._local_pitch = 0.0
        battle._local_roll = 0.0
        battle._local_speed = 2.0
        battle._local_turn_speed = 0.1
        battle._reset_full_turret_sniper_aim = mock.Mock()
        battle._update_local_tracks = mock.Mock(
            side_effect=RuntimeError('track boundary failed'))
        entity = types.SimpleNamespace()

        with contextlib.redirect_stdout(io.StringIO()) as log:
            first = battle._update_local_presentation(entity, 0.1)
            second = battle._update_local_presentation(entity, 0.1)

        self.assertEqual((3.0, 4.0, 5.0),
                         (first.x, first.y, first.z))
        self.assertEqual((3.0, 4.0, 5.0),
                         (second.x, second.y, second.z))
        self.assertEqual(
            2, runtime.compatibility.set_vehicle_pose_overlay.call_count)
        battle._update_local_tracks.assert_called_once_with(entity)
        self.assertEqual(
            1, log.getvalue().count(
                'optional local track animation disabled for this round'))


    def test_damage_info_is_skipped_when_the_battle_gui_is_gone(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        record = {'engine_id': 10, 'local': True}
        events = [{'kind': 'crew', 'name': 'driver',
                   'state': 'destroyed', 'cause': 'drowning'}]
        runtime.app_loader = types.SimpleNamespace(
            getDefBattleApp=lambda: None)

        with mock.patch.object(
                battle, '_critical_extra_index', return_value=7):
            self.assertFalse(battle._present_critical(record, events, 99))

        self.assertEqual([], battle._avatar.damage_info)

    def test_damage_info_flash_failure_does_not_end_the_round(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(10, _Descriptor(), _Vector(), (0, 0, 0),
                          {'health': 500})
        runtime.bigworld.entities[10] = entity
        record = {'engine_id': 10, 'local': True}
        events = [{'kind': 'crew', 'name': 'driver',
                   'state': 'destroyed', 'cause': 'drowning'}]
        battle._avatar.showVehicleDamageInfo = mock.Mock(
            side_effect=RuntimeError(
                'PyGFxValue - Failed to invoke method as_updateDeviceState'))

        with contextlib.redirect_stdout(io.StringIO()):
            with mock.patch.object(
                    battle, '_critical_extra_index', return_value=7):
                self.assertFalse(battle._present_critical(record, events, 99))

    def test_damage_info_range_failure_is_never_fatal_or_repeated(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar

        with contextlib.redirect_stdout(io.StringIO()) as log:
            self.assertFalse(battle._show_damage_info(10, 999, 0, 11))
            self.assertFalse(battle._show_damage_info(10, 999, 0, 11))

        self.assertEqual([], battle._avatar.damage_info)
        self.assertEqual(
            1, log.getvalue().count(
                'optional damage-info presentation disabled for this '
                'round'))

    def test_far_bots_sample_the_suspension_less_than_near_bots(self):
        near = {'id': 1, 'x': 10.0, 'y': 2.0, 'z': 0.0, 'yaw': 0.0,
                'pitch': 0.0, 'roll': 0.0, 'half_length': 3.5,
                'half_width': 1.7, 'airborne': False, 'grounded_once': True}
        far = dict(near, id=2, x=400.0)
        counts = {}
        for name, state in (('near', near), ('far', far)):
            bots = bot_runtime.BotRuntime.__new__(bot_runtime.BotRuntime)
            bots._load_level = 0
            bots._probe_totals = [0, 0, 0, 0, 0]
            bots._probe_started = lambda: None
            bots._probe_finished = lambda index, started: None
            bots._physics_ground_probe = lambda x, z, hint: 2.0
            bots.set_camera_position((0.0, 0.0, 0.0))
            for step in range(20):
                state['x'] += 0.5
                bots._update_slope_pose(state)
            counts[name] = bots._probe_totals[3]

        self.assertGreater(counts['near'], counts['far'])
        self.assertGreater(counts['near'], 0)


if __name__ == '__main__':
    unittest.main()


class RecentIdSetTests(unittest.TestCase):
    def test_recent_ids_reject_redelivery_without_growing(self):
        seen = battle_runtime_module._RecentIdSet(limit=3)

        for value in ('1:1:0', '1:1:1', '1:2:0'):
            self.assertTrue(seen.add(value))
        self.assertFalse(seen.add('1:1:0'))
        self.assertEqual(3, len(seen))

        self.assertTrue(seen.add('1:3:0'))

        self.assertEqual(3, len(seen))
        self.assertNotIn('1:1:0', seen)
        self.assertIn('1:3:0', seen)


class ArenaIdentityTests(unittest.TestCase):
    def test_the_avatar_carries_the_resolved_standard_arena_id(self):
        # ArenaType.id is (gameplayID << 16) | geometryID, and the battle GUI
        # resolves bases and the minimap from it.  Zero named geometry 0's
        # standard bases, which drew flags belonging to another map.
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._runtime.arena_cache = {
            0x10005: types.SimpleNamespace(
                id=0x10005, geometryName='01_karelia',
                gameplayName='ctf', gameplayID=1),
            0x20005: types.SimpleNamespace(
                id=0x20005, geometryName='01_karelia',
                gameplayName='domination', gameplayID=2),
        }

        arena_type = battle._standard_arena('01_karelia')

        self.assertIsNotNone(arena_type)
        self.assertEqual(0x10005, arena_type.id)
        self.assertEqual('ctf', arena_type.gameplayName)


class StunStateTests(unittest.TestCase):
    def _battle(self, local):
        runtime = _runtime()
        runtime.bigworld.now = 100.0
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        entity = _Vehicle(
            10, _Descriptor(), _Vector(), (0, 0, 0), {'health': 500})
        runtime.bigworld.entities[10] = entity
        record = {
            'engine_id': 10, 'kind': 'player' if local else 'bot',
            'network_id': 1 if local else 7, 'local': bool(local),
            'ready': True, 'state': {'health': 500, 'alive': True}}
        battle._records = {
            ('player:1' if local else 'bot:7'): record}
        return battle, runtime, record, entity

    def test_local_snapshot_uses_absolute_server_time_and_clears(self):
        battle, runtime, record, entity = self._battle(True)
        battle._projectile_server_time_ms = 15000
        battle._projectile_server_local_time = 100.0
        state = {
            'stun_end_server_time_ms': 20000,
            'stun_attacker_kind': 'bot', 'stun_attacker_id': 7}

        self.assertTrue(battle._apply_stun_state(record, state))

        self.assertEqual(105.0, entity.stunInfo)
        runtime.bigworld.avatar.guiSessionProvider.invalidateVehicleState.\
            assert_called_once_with('stun', 5.0)
        self.assertFalse(battle._apply_stun_state(record, state))

        self.assertTrue(battle._apply_stun_state(record, {
            'stun_end_server_time_ms': 0,
            'stun_attacker_kind': '', 'stun_attacker_id': 0}))
        self.assertEqual(0.0, entity.stunInfo)
        self.assertEqual(
            mock.call('stun', 0.0),
            runtime.bigworld.avatar.guiSessionProvider.
            invalidateVehicleState.call_args)

    def test_ordered_remote_stun_merges_before_native_feedback(self):
        battle, runtime, record, entity = self._battle(False)
        battle.state = 'running'
        event = {
            'event_id': '1:7:0', 'kind': 'stun', 'active': True,
            'target_kind': 'bot', 'target_id': 7,
            'attacker_kind': 'player', 'attacker_id': 1,
            'stun_end_server_time_ms': 22000,
        }

        self.assertTrue(battle.on_events({
            'server_time_ms': 15000, 'events': [event]}))

        self.assertEqual(22000, record['state'][
            'stun_end_server_time_ms'])
        self.assertEqual(107.0, entity.stunInfo)
        runtime.bigworld.avatar.guiSessionProvider.shared.feedback.\
            invalidateStun.assert_called_once_with(10, 7.0)
        self.assertIn('1:7:0', battle._applied_event_ids)

    def test_stock_vehicle_stun_callback_remains_the_presentation_owner(self):
        battle, runtime, record, entity = self._battle(True)
        battle._projectile_server_time_ms = 15000
        battle._projectile_server_local_time = 100.0
        entity.set_stunInfo = mock.Mock()

        self.assertTrue(battle._apply_stun_state(record, {
            'stun_end_server_time_ms': 19000,
            'stun_attacker_kind': 'bot', 'stun_attacker_id': 7}))

        entity.set_stunInfo.assert_called_once_with(0.0)
        runtime.bigworld.avatar.guiSessionProvider.invalidateVehicleState.\
            assert_not_called()

    def test_hidden_worker_tracks_stun_without_gui_feedback(self):
        battle, runtime, record, entity = self._battle(False)
        battle._worker_mode = True
        battle._projectile_server_time_ms = 15000
        battle._projectile_server_local_time = 100.0

        self.assertTrue(battle._apply_stun_state(record, {
            'stun_end_server_time_ms': 18000,
            'stun_attacker_kind': 'player', 'stun_attacker_id': 1}))

        self.assertEqual(103.0, entity.stunInfo)
        runtime.bigworld.avatar.guiSessionProvider.shared.feedback.\
            invalidateStun.assert_not_called()


class AssistFeedTests(unittest.TestCase):
    def _battle(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._records = {
            'player:1': {'engine_id': 10, 'local': True,
                         'state': {'team': 1, 'alive': True}},
            'bot:7': {'engine_id': 17, 'local': False,
                      'state': {'team': 2, 'alive': True}},
        }
        return battle, runtime

    def _event(self, **overrides):
        event = {
            'kind': 'assist', 'category': 'track',
            'assister_kind': 'player', 'assister_id': 1,
            'attacker_kind': 'bot', 'attacker_id': 3,
            'target_kind': 'bot', 'target_id': 7, 'damage': 240,
        }
        event.update(overrides)
        return event

    def test_a_local_assist_reaches_the_stock_damage_log(self):
        battle, runtime = self._battle()

        self.assertTrue(battle._apply_assist_event(self._event()))

        events = runtime.bigworld.avatar.battle_events[-1]
        self.assertEqual(1, len(events))
        entry = events[0]
        # RADIO_ASSIST and TRACK_ASSIST both map to
        # PLAYER_ASSIST_TO_KILL_ENEMY, whose converter is _unpackDamage.
        self.assertEqual(
            int(runtime.battle_feedback_common.BATTLE_EVENT_TYPE.TRACK_ASSIST),
            entry['eventType'])
        self.assertEqual(17, entry['targetID'])
        self.assertEqual(240, entry['details'] >> 16)

    def test_radio_and_stun_categories_use_their_own_event_type(self):
        battle, runtime = self._battle()
        types_ = runtime.battle_feedback_common.BATTLE_EVENT_TYPE

        battle._apply_assist_event(self._event(category='radio'))
        self.assertEqual(
            int(types_.RADIO_ASSIST),
            runtime.bigworld.avatar.battle_events[-1][0]['eventType'])

        battle._apply_assist_event(self._event(category='stun'))
        self.assertEqual(
            int(types_.STUN_ASSIST),
            runtime.bigworld.avatar.battle_events[-1][0]['eventType'])

    def test_an_assist_by_somebody_else_is_not_published(self):
        battle, runtime = self._battle()
        before = len(runtime.bigworld.avatar.battle_events)

        self.assertFalse(battle._apply_assist_event(
            self._event(assister_kind='bot', assister_id=7)))

        self.assertEqual(before, len(runtime.bigworld.avatar.battle_events))

    def test_an_unknown_assist_category_is_refused(self):
        battle, unused_runtime = self._battle()

        with self.assertRaises(RuntimeError):
            battle._apply_assist_event(self._event(category='ramming'))


class SpottedReportTests(unittest.TestCase):
    def _battle(self):
        battle = BattleRuntime(_runtime())
        sent = []
        battle.client = types.SimpleNamespace(
            team=1, player_id=1,
            send_spotted_report=lambda targets: sent.append(
                list(targets)) or True)
        return battle, sent

    def test_visible_spotted_set_remains_presentation_only(self):
        battle, sent = self._battle()
        bot = {'kind': 'bot', 'network_id': 7, 'engine_id': 17}

        self.assertFalse(battle._publish_spotted_targets([bot]))
        self.assertEqual([], sent)
        self.assertEqual((('bot', 7),), battle._spotted_signature)

        # The local signature still suppresses redundant presentation work,
        # but a visible process never publishes a canonical spotting verdict.
        self.assertFalse(battle._publish_spotted_targets([bot]))
        self.assertEqual([], sent)

        self.assertFalse(battle._publish_spotted_targets([]))
        self.assertEqual((), battle._spotted_signature)
        self.assertEqual([], sent)

    def test_a_record_without_a_network_identity_is_skipped(self):
        battle, sent = self._battle()

        battle._publish_spotted_targets([
            {'kind': 'bot', 'network_id': None, 'engine_id': 17},
            {'kind': 'scenery', 'network_id': 3, 'engine_id': 18},
        ])

        self.assertEqual((), battle._spotted_signature)
        self.assertEqual([], sent)


class MemoryRankingTests(unittest.TestCase):
    """The first baseline reported 1.3 MB because the sizer stopped at the
    object boundary and the sample ran before BotRuntime existed."""

    def setUp(self):
        self.deep_size = battle_runtime_module._deep_size

    def test_a_container_is_counted_once_across_two_roots(self):
        shared = [0] * 500
        seen = set()

        first = self.deep_size({'a': shared}, seen)
        second = self.deep_size({'b': shared}, seen)

        self.assertGreater(first, second)

    def test_the_sizer_walks_into_this_port_s_own_objects(self):
        holder = battle_runtime_module._RecentIdSet()
        for index in range(400):
            holder.add('1:%d:0' % index)

        # A bare getsizeof of the object returns about 32 bytes.
        self.assertGreater(self.deep_size(holder), 8000)

    def test_the_sizer_stops_at_a_foreign_object(self):
        foreign = types.SimpleNamespace(payload=[0] * 5000)

        self.assertLess(self.deep_size(foreign), 500)

    def test_a_cycle_terminates(self):
        node = {}
        node['self'] = node

        self.assertGreater(self.deep_size(node), 0)

    def test_the_recent_id_window_stays_bounded(self):
        holder = battle_runtime_module._RecentIdSet(limit=64)
        for index in range(500):
            holder.add('1:%d:0' % index)

        self.assertEqual(64, len(holder))

    def test_default_gameplay_does_not_walk_the_heap_for_diagnostics(self):
        battle = BattleRuntime.__new__(BattleRuntime)
        battle._config = {'debug_logging': False}
        battle._memory_rows = mock.Mock(
            side_effect=AssertionError('heap walk must stay opt-in'))

        self.assertFalse(battle._report_memory('round_end'))
        battle._memory_rows.assert_not_called()


class DescriptorReuseTests(unittest.TestCase):
    """Two descriptors were built per bot, and the factory pins every one."""

    def test_one_descriptor_is_built_per_vehicle_type(self):
        built = []

        class _Vehicles(object):
            @staticmethod
            def VehicleDescr(typeName=None, compactDescr=None):
                name = typeName or compactDescr
                if typeName is not None:
                    built.append(typeName)
                return types.SimpleNamespace(
                    name=name, type=types.SimpleNamespace(name=name),
                    makeCompactDescr=lambda: name)

        runtime = BattleRuntime.__new__(BattleRuntime)
        runtime._descriptor_cache = {}
        runtime._prepared_vehicle_names = []
        runtime._unusable_vehicles_reported = set()
        runtime._config = {'vehicle': 'ussr:T-34'}
        runtime._runtime = types.SimpleNamespace(vehicles=_Vehicles)
        runtime._remote_factory = types.SimpleNamespace(
            prepare_descriptor=lambda descriptor: descriptor)

        first = runtime._resolve_descriptor('ussr:T-34')
        second = runtime._resolve_descriptor('ussr:T-34')
        other = runtime._resolve_descriptor('germany:PzVI')

        self.assertIs(first, second)
        self.assertIsNot(first, other)
        self.assertEqual(['ussr:T-34', 'germany:PzVI'], built)

    def test_bot_top_fitting_precedes_native_descriptor_preparation(self):
        events = []
        descriptor = types.SimpleNamespace(
            type=types.SimpleNamespace(name='ussr:T-34'),
            makeCompactDescr=lambda: 'top-fitting')
        canonical = types.SimpleNamespace(
            type=types.SimpleNamespace(name='ussr:T-34'))
        runtime = BattleRuntime.__new__(BattleRuntime)
        runtime._runtime = types.SimpleNamespace(
            vehicles=types.SimpleNamespace(
                VehicleDescr=lambda typeName=None, compactDescr=None: (
                    events.append(('construct', typeName, compactDescr)) or
                    (descriptor if typeName is not None else canonical))))
        runtime._remote_factory = types.SimpleNamespace(
            prepare_descriptor=lambda value: (
                events.append(('prepare', value)) or value))

        with mock.patch.object(
                battle_runtime_module.vehicle_configuration,
                'install_top_modules',
                side_effect=lambda value: (
                    events.append(('top', value)) or value)):
            result = runtime._prepare_vehicle_descriptor('ussr:T-34')

        self.assertIs(canonical, result)
        self.assertEqual([
            ('construct', 'ussr:T-34', None), ('top', descriptor),
            ('construct', None, 'top-fitting'), ('prepare', canonical),
        ], events)


class UnusableVehicleTests(unittest.TestCase):
    """A vehicle this client cannot load must not cost the round."""

    def _battle(self, broken_name):
        def VehicleDescr(typeName=None, compactDescr=None):
            return _Descriptor(typeName or compactDescr, loaded=True)

        def prepare_descriptor(descriptor):
            if descriptor.name == broken_name:
                raise RuntimeError(
                    '#1513 vehicle hit tester BSP load failed: wrong '
                    'collision model')
            return descriptor

        battle = BattleRuntime.__new__(BattleRuntime)
        battle._descriptor_cache = {}
        battle._prepared_vehicle_names = []
        battle._unusable_vehicles_reported = set()
        battle._config = {'vehicle': 'ussr:R11_MS-1'}
        battle._runtime = types.SimpleNamespace(
            vehicles=types.SimpleNamespace(VehicleDescr=VehicleDescr))
        battle._remote_factory = types.SimpleNamespace(
            prepare_descriptor=prepare_descriptor)
        return battle

    def test_baked_blacklist_keeps_an_unloadable_type_out_of_the_lineup(self):
        name = sorted(
            battle_runtime_module.vehicle_blacklist.UNUSABLE_VEHICLES)[0]
        entry = types.SimpleNamespace(name=name, level=8, tags=('heavyTank',))

        self.assertTrue(BattleRuntime._vehicle_excluded(entry))
        self.assertFalse(BattleRuntime._vehicle_excluded(
            types.SimpleNamespace(
                name='ussr:R11_MS-1', level=1, tags=('lightTank',))))

    def test_a_failed_bot_descriptor_yields_a_full_battle_on_a_substitute(self):
        battle = self._battle('germany:broken')
        roster = [
            {'id': 11, 'team': 1, 'slot': 0, 'vehicle': 'ussr:R11_MS-1'},
            {'id': 12, 'team': 2, 'slot': 0, 'vehicle': 'germany:broken'},
            {'id': 13, 'team': 2, 'slot': 1, 'vehicle': 'ussr:T-34'},
        ]
        bots = bot_runtime.BotRuntime(
            1, descriptor_resolver=battle._resolve_descriptor,
            vehicle_selector=lambda raw: raw['vehicle'],
            spawn_resolver=lambda team, slot: ((0.0, 0.0, 0.0), 0.0),
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            baked_graph=_runtime().navigation_graph_loader('01_karelia'))

        with contextlib.redirect_stdout(io.StringIO()) as log:
            bots.battle_start({
                'round_id': 7, 'map': '01_karelia', 'bot_authority_id': 1,
                'bots': roster})

        self.assertEqual([11, 12, 13], sorted(bots.states))
        self.assertEqual(
            'ussr:R11_MS-1', bots._descriptors[12].name)
        self.assertIn('germany:broken cannot be loaded', log.getvalue())

    def test_a_bot_descriptor_without_a_substitute_fails_the_batch(self):
        battle = self._battle('germany:broken')
        battle._remote_factory = types.SimpleNamespace(
            prepare_descriptor=lambda descriptor: (_ for _ in ()).throw(
                RuntimeError('#1513 vehicle hit tester BSP load failed')))
        bots = bot_runtime.BotRuntime(
            1, descriptor_resolver=battle._resolve_descriptor,
            vehicle_selector=lambda raw: raw['vehicle'],
            spawn_resolver=lambda team, slot: ((0.0, 0.0, 0.0), 0.0),
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            baked_graph=_runtime().navigation_graph_loader('01_karelia'))

        with self.assertRaisesRegex(
                RuntimeError,
                'bot 12 vehicle germany:broken descriptor is unavailable'):
            bots.battle_start({
                'round_id': 7, 'map': '01_karelia', 'bot_authority_id': 1,
                'bots': [{'id': 12, 'team': 2, 'slot': 0,
                          'vehicle': 'germany:broken'}]})

        self.assertEqual([], sorted(bots.states))
        self.assertFalse(bots._manifest_sent)
        self.assertIsNone(bots.adapter)


class LocalBattleDescriptorTests(unittest.TestCase):
    """The battle measures the tank the garage panel measured."""

    def _runtime(self, fitting, worker=False):
        def VehicleDescr(typeName=None, compactDescr=None):
            if compactDescr is not None:
                return types.SimpleNamespace(source='fitted:%s' % compactDescr)
            return types.SimpleNamespace(source='stock:%s' % typeName)

        runtime = BattleRuntime.__new__(BattleRuntime)
        runtime._runtime = types.SimpleNamespace(
            vehicles=types.SimpleNamespace(VehicleDescr=VehicleDescr))
        runtime._garage_loadout = {'fitting': fitting}
        runtime._worker_mode = bool(worker)
        return runtime

    def test_the_mounted_compact_descriptor_wins(self):
        runtime = self._runtime(('CD', 'ussr:T-34'))

        descriptor = runtime._local_battle_descriptor('ussr:T-34')

        self.assertEqual('fitted:CD', descriptor.source)

    def test_worker_another_vehicle_falls_back_to_the_stock_fitting(self):
        runtime = self._runtime(('CD', 'ussr:T-34'), worker=True)

        descriptor = runtime._local_battle_descriptor('germany:PzVI')

        self.assertEqual('stock:germany:PzVI', descriptor.source)

    def test_worker_without_garage_falls_back_to_the_stock_fitting(self):
        runtime = self._runtime(None, worker=True)

        descriptor = runtime._local_battle_descriptor('ussr:T-34')

        self.assertEqual('stock:ussr:T-34', descriptor.source)

    def test_visible_client_rejects_a_mismatched_garage_vehicle(self):
        runtime = self._runtime(('CD', 'ussr:T-34'))

        with self.assertRaisesRegex(
                RuntimeError, 'does not match germany:PzVI'):
            runtime._local_battle_descriptor('germany:PzVI')

    def test_visible_client_rejects_a_missing_garage_descriptor(self):
        runtime = self._runtime(None)

        with self.assertRaisesRegex(
                RuntimeError, 'does not match ussr:T-34'):
            runtime._local_battle_descriptor('ussr:T-34')
