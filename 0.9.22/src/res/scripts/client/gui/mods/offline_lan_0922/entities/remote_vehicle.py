from __future__ import print_function

"""0.8.2-style authoritative remote vehicles on the #1513 renderer.

The retail ``Vehicle`` entity is the local player's physics carrier.  A
remote retail Vehicle expects server-owned filter snapshots that an offline
client cannot manufacture through the public #1513 API.  The mature 0.8.2
battle therefore kept a Python vehicle object for gameplay and attached its
model to a separate ``OfflineEntity``.  This module preserves that boundary,
while using #1513's verified compound-model assembler.
"""

import math
import sys
import time
import weakref
from collections import namedtuple


def _blend_angle(source, target, ratio):
    """Interpolate along the shortest arc so a wrap never spins the hull."""
    delta = (float(target) - float(source) + math.pi) % (
        2.0 * math.pi) - math.pi
    return float(source) + delta * ratio

# Native pose objects this process has allocated.  Allocating a fresh pair per
# accepted pose walked a 2 GB client into its address-space ceiling, so a
# vehicle now owns its animation and both keyframe matrices for its whole life.
_pose_object_allocations = 0

# The shortest interval a Math.MatrixAnimation keyframe pair may span.
_MINIMUM_KEYFRAME_SECONDS = 0.001


def pose_animation_writes():
    """Return how many native pose objects this process has allocated."""
    return _pose_object_allocations


def reset_pose_animation_writes():
    """Start a new round's count, so the number is not a process total."""
    global _pose_object_allocations
    _pose_object_allocations = 0

from gui.mods.offline_lan_0922 import tank_collision

try:
    _STRING_TYPES = (basestring,)
except NameError:
    _STRING_TYPES = (str,)


# Exact value contract returned by #1513 ``Vehicle.collideSegment``.  The
# stock ProjectileMover uses both tuple indexing and these named fields.
_SegmentCollisionResult = namedtuple(
    'SegmentCollisionResult', ('dist', 'hitAngleCos', 'armor'))

# Exact value contract returned by #1513 ``Vehicle.collideSegmentExt``.
# ``AvatarInputHandler.gun_marker_ctrl`` reads all four named fields on every
# gun-rotator tick.  A Python object carrying the component descriptor under a
# private adapter name is not equivalent: the missing ``compName`` aborts the
# native tick and freezes mouse aim, dispersion and target-lock feedback.
_SegmentCollisionResultExt = namedtuple(
    'SegmentCollisionResultExt',
    ('dist', 'hitAngleCos', 'matInfo', 'compName'))

# Authority-only evidence kept outside the exact retail collision ABI. The
# native BSP tuple's second value is a component-local surface normal, not a
# triangle. Keeping it in a wrapper prevents a five-field value from reaching
# stock gun-marker and ProjectileMover callers that require exactly four.
_VehicleCollisionEvidence = namedtuple(
    'VehicleCollisionEvidence', ('collision', 'worldNormal'))


class _AliveFlag(object):

    def __init__(self, value=True):
        self.value = bool(value)

    def __call__(self):
        return self.value

    def __nonzero__(self):
        return self.value

    def __bool__(self):
        return self.value


class _Signal(object):
    """Small Event-compatible signal for marker consumers."""

    def __init__(self):
        self._handlers = []

    def __iadd__(self, handler):
        if handler not in self._handlers:
            self._handlers.append(handler)
        return self

    def __isub__(self, handler):
        try:
            self._handlers.remove(handler)
        except ValueError:
            pass
        return self

    def __call__(self, *args, **kwargs):
        for handler in tuple(self._handlers):
            handler(*args, **kwargs)


class _ModelsDescription(object):

    def __init__(self, owner):
        self._owner = owner

    def __getitem__(self, unused_name):
        return {'model': self._owner.model}


class _DamageState(object):

    isCurrentModelDamaged = False


class _RemoteEngineAudition(object):
    """Exact sound-object boundary consumed by #1513 shot effects."""

    def __init__(self, owner):
        self._owner = owner
        self._objects = {}

    def getSoundObject(self, index):
        model = self._owner.model
        if model is None:
            raise RuntimeError('remote vehicle sound requested without model')
        index = int(index)
        sound_object = self._objects.get(index)
        if sound_object is not None:
            return sound_object
        import SoundGroups
        factory = getattr(SoundGroups.g_instance, 'WWgetSoundObject', None)
        if not callable(factory):
            raise RuntimeError('#1513 WWgetSoundObject is unavailable')
        node = model.node('HP_gunFire')
        sound_object = factory(
            'offline_lan_vehicle_%d_sound_%d' % (self._owner.id, index),
            node)
        if sound_object is None:
            raise RuntimeError('#1513 remote vehicle sound object is missing')
        self._objects[index] = sound_object
        return sound_object

    def detach(self):
        self._objects.clear()


class _RemoteAppearance(object):

    def __init__(self, math_module, owner):
        self._math = math_module
        self._owner = owner
        self.onModelChanged = _Signal()
        self.turretMatrix = math_module.Matrix()
        self.turretMatrix.setIdentity()
        self.gunMatrix = math_module.Matrix()
        self.gunMatrix.setIdentity()
        self.compoundModel = None
        self.models = []
        self.modelsDesc = _ModelsDescription(owner)
        # setupTurretRotations reads this exact CompoundAppearance contract.
        self.damageState = _DamageState()
        self.isLoaded = False
        # Observed-vehicle UI handlers read this exact CompoundAppearance
        # field while constructing their speed/RPM state handlers.
        self.gear = 0
        self.isInWater = False
        self.isUnderwater = False
        self.gunRecoil = None
        self.engineAudition = _RemoteEngineAudition(owner)
        self._bound_effects = None

    @property
    def typeDescriptor(self):
        return self._owner.typeDescriptor

    @property
    def boundEffects(self):
        """Own the compound the #1513 fire extra plays its flame on."""
        if self._bound_effects is None:
            if self.compoundModel is None:
                raise RuntimeError(
                    'remote bound effects requested without a compound')
            from helpers import bound_effects
            self._bound_effects = bound_effects.ModelBoundEffects(
                self.compoundModel)
        return self._bound_effects

    def switchFireVibrations(self, unused_start):
        """#1513 routes fire vibrations to the player's peripherals only."""
        return None

    def onSiegeStateChanged(self, state):
        """Mirror the CompoundAppearance hook used by stock Vehicle."""
        self.siegeState = int(state)

    def addDamageSticker(self, code, component_name, sticker_id,
                         segment_start, segment_end):
        """Delegate the stock CompoundAppearance damage-sticker contract."""
        stickers = self._owner._vehicle_stickers
        add_sticker = getattr(stickers, 'addDamageSticker', None)
        if not callable(add_sticker):
            raise RuntimeError('remote damage-sticker owner is unavailable')
        return add_sticker(
            code, component_name, sticker_id, segment_start, segment_end)

    def attach(self, model):
        self.compoundModel = model
        self.models = [model]
        self.isLoaded = True
        self.onModelChanged()

    def detach(self):
        self.engineAudition.detach()
        effects = self._bound_effects
        if effects is not None:
            effects.destroy()
            if self._bound_effects is effects:
                self._bound_effects = None
        self.compoundModel = None
        self.models = []
        self.isLoaded = False
        self.onModelChanged()

    def abandon(self):
        """Release the sound objects and forget the freed compound.

        A ``_WWISE.SoundObject`` belongs to the sound engine and the WorldApp
        scene, not to the entity manager, so it must go while ``guiModsFini``
        still has a live scene.  The shutdown GC destroys it after the scene
        pointer is gone.
        """
        self.engineAudition.detach()
        self._bound_effects = None
        self.compoundModel = None
        self.models = []
        self.isLoaded = False
        self.gunRecoil = None

    def changeVisibility(self, visible):
        """Expose the exact #1513 CompoundAppearance visibility boundary."""
        if self.compoundModel is None:
            raise RuntimeError('remote compound model is unavailable')
        self.compoundModel.visible = bool(visible)
        return True

    def showDamageFromShot(self, *unused_args, **unused_kwargs):
        return None

    def showDamageFromExplosion(self, *unused_args, **unused_kwargs):
        return None

    def recoil(self):
        recoil = self.gunRecoil
        callback = getattr(recoil, 'recoil', None)
        if callable(callback):
            callback()


class _RemoteShotPresenter(object):
    """Shared #1513 tracer and gun-recoil resources for remote vehicles.

    Muzzle flash and shot sound stay on the stock ``ShowShooting`` extra.
    The retail client receives a separate tracer message online, so the
    offline relay must also feed ``ProjectileMover`` explicitly.  This is the
    #1513 adaptation of the mature 0.8.2 remote-shot presentation path.
    """

    # A stock gun can emit a short autocannon burst, so keep a generous
    # immediate allowance.  Sustained edited reloads are presentation-only
    # load and refill more slowly than they can create native particles.
    _VISUAL_BURST_CAPACITY = 16.0
    _VISUAL_REFILL_PER_SECOND = 5.0
    _MAX_ACTIVE_PER_ATTACKER = 24
    _MAX_ACTIVE_TOTAL = 128

    def __init__(self, bigworld, math_module, model_assembler, space_id):
        self._bigworld = bigworld
        self._math = math_module
        self._model_assembler = model_assembler
        self._space_id = int(space_id)
        self._mover = None
        self._next_shot_id = 1000000
        self._projectile_shots = {}
        self._projectile_order = []
        self._visual_budgets = {}
        self._visual_admissions = {}
        self._failure_stages = set()
        self._launches_enabled = True
        self._explosions_enabled = True
        self._closed = False

    def admit_visual(self, attacker_id, projectile_id=None, now=None):
        """Bound cosmetic work without rejecting the authoritative shot.

        One admission owns the muzzle, tracer and terminal effect family for
        a projectile.  Callers may ask before entering native ``showShooting``;
        ``play_canonical`` then reuses the stored decision instead of charging
        the same projectile twice.
        """
        if self._closed or not self._launches_enabled:
            return False
        try:
            attacker_id = int(attacker_id)
        except (TypeError, ValueError, OverflowError):
            return False
        if attacker_id <= 0:
            return False
        key = None
        if projectile_id is not None:
            try:
                key = str(projectile_id)
            except Exception:
                return False
            if not key or len(key) > 128:
                return False
            decision = self._visual_admissions.get(key)
            if decision is not None:
                return bool(decision[1]) if decision[0] == attacker_id else False
        moment = self._visual_clock(now)
        state = self._visual_budgets.get(attacker_id)
        if state is None:
            tokens = self._VISUAL_BURST_CAPACITY
        else:
            tokens, updated = state
            elapsed = max(0.0, moment - updated)
            tokens = min(
                self._VISUAL_BURST_CAPACITY,
                tokens + elapsed * self._VISUAL_REFILL_PER_SECOND)
        admitted = tokens >= 1.0 - 1.0e-9
        if admitted:
            tokens = max(0.0, tokens - 1.0)
        self._visual_budgets[attacker_id] = (tokens, moment)
        if key is not None:
            self._visual_admissions[key] = (attacker_id, admitted)
        return admitted

    def _visual_clock(self, now):
        moment = self._finite_float(now)
        if moment is not None:
            return moment
        callback = getattr(self._bigworld, 'time', None)
        if callable(callback):
            try:
                moment = self._finite_float(callback())
            except Exception:
                moment = None
        return time.time() if moment is None else moment

    def setup_recoil(self, vehicle):
        if vehicle.model is None or vehicle.typeDescriptor is None:
            return None
        assemble = getattr(self._model_assembler, 'assembleRecoil', None)
        if not callable(assemble):
            return None
        try:
            # #1513 replaced 0.8.2's WGGunRecoil fashion with the compound
            # model assembler's Vehicular.RecoilAnimator.  ``None`` is the
            # same valid no-LOD-link value accepted by createGunAnimator.
            assemble(vehicle.appearance, None)
            recoil = vehicle.appearance.gunRecoil
            vehicle._gun_recoil = recoil
            return recoil
        except Exception:
            vehicle._gun_recoil = None
            return None

    def setup_turret_rotations(self, vehicle):
        setup = getattr(self._model_assembler, 'setupTurretRotations', None)
        if not callable(setup):
            raise RuntimeError(
                '#1513 model assembler has no setupTurretRotations')
        # Updating Matrix values alone does not move compound-model nodes.
        # This is the exact binding used by CompoundAppearance after refresh.
        setup(vehicle.appearance)

    def play_tracer(self, vehicle):
        if self._closed or vehicle.model is None:
            return False
        canonical_names = (
            '_offlineLANShotOrigin', '_offlineLANShotVelocity',
            '_offlineLANShotGravity', '_offlineLANShotMaxDistance')
        if all(hasattr(vehicle, name) for name in canonical_names):
            shot_id = self.play_canonical(
                vehicle.typeDescriptor, vehicle._offlineLANShotIndex,
                vehicle._offlineLANShotOrigin,
                vehicle._offlineLANShotVelocity,
                vehicle._offlineLANShotGravity,
                vehicle._offlineLANShotMaxDistance, vehicle.id,
                getattr(vehicle, '_offlineLANProjectileID', None),
                getattr(
                    vehicle, '_offlineLANShotReferenceOrigin', None),
                getattr(
                    vehicle, '_offlineLANShotReferenceVelocity', None))
            return bool(shot_id)
        shot = self._active_shot(vehicle)
        speed = _component_value(shot, 'speed')
        gravity = _component_value(shot, 'gravity')
        if shot is None or speed is None or gravity is None:
            return False
        try:
            start = self._muzzle_position(vehicle)
            direction = self._direction(vehicle)
            velocity = direction.scale(float(speed))
            maximum = _component_value(shot, 'maxDistance', 5000.0)
            shot_id = self.play_canonical(
                vehicle.typeDescriptor, vehicle._offlineLANShotIndex,
                start, velocity, gravity, maximum or 5000.0, vehicle.id)
            # Preserve the pre-ledger RemoteVehicle.showShooting contract.
            # Canonical callers receive the stable visual id itself.
            return bool(shot_id)
        except Exception:
            return False

    def play_canonical(self, descriptor, shell_index, origin, velocity,
                       gravity, max_distance, attacker_id,
                       projectile_id=None, reference_position=None,
                       reference_velocity=None, is_ricochet=False):
        """Present one canonical launch through the exact #1513 mover ABI.

        The origin and velocity belong to the authoritative launch event.
        They must never be recomputed from the vehicle's presentation pose,
        which may already have advanced by the time a relay event arrives.
        Invalid native-boundary values fail closed before ProjectileMover is
        constructed or called.
        """
        if self._closed or not self._launches_enabled:
            return False
        shot = self._shot_at(descriptor, shell_index)
        shell = _component_value(shot, 'shell')
        effects_index = _component_value(shell, 'effectsIndex')
        if shot is None or shell is None or effects_index is None:
            return False
        visual_start = self._finite_vector(origin)
        reference_start = (visual_start if reference_position is None else
                           self._finite_vector(reference_position))
        reference_velocity = self._finite_vector(
            velocity if reference_velocity is None else reference_velocity)
        gravity = self._finite_float(gravity)
        maximum = self._finite_float(max_distance)
        try:
            attacker_id = int(attacker_id)
        except (TypeError, ValueError, OverflowError):
            return False
        existing = None
        if projectile_id is not None:
            try:
                projectile_id = str(projectile_id)
            except Exception:
                return False
            if not projectile_id or len(projectile_id) > 128:
                return False
            existing = self._projectile_shots.get(projectile_id)
        if (visual_start is None or reference_start is None or
                reference_velocity is None or gravity is None or
                maximum is None or gravity < 0.0 or maximum <= 0.0 or
                attacker_id <= 0):
            return False
        if (existing is None and
                not self.admit_visual(attacker_id, projectile_id)):
            return False
        mover = None
        shot_id = None
        try:
            from items import vehicles
            effects_descr = vehicles.g_cache.shotEffects[effects_index]
            if effects_descr is None:
                return False
            try:
                artillery = effects_descr.get('artilleryID') is not None
            except AttributeError:
                return False
            mover = self._projectile_mover()
            if mover is None:
                return False
            self._prune_stale_projectiles(mover)
            existing = (self._projectile_shots.get(projectile_id)
                        if projectile_id is not None else None)
            if existing is not None:
                existing_id, existing_artillery = existing[:2]
                if existing_artillery:
                    return existing_id
                active = getattr(
                    mover, '_ProjectileMover__projectiles', None)
                if (isinstance(active, dict) and
                        existing_id in active):
                    return existing_id
                # The native simulator discarded or retired the projectile
                # without an authoritative terminal.  Drop only our stale
                # dedupe entry so the active snapshot can recreate it.
                self._remove_projectile_mapping(projectile_id)
            if not self._ensure_visual_capacity(mover, attacker_id):
                return False
            camera = getattr(self._bigworld, 'camera', None)
            camera = camera() if callable(camera) else None
            camera_position = self._finite_vector(
                getattr(camera, 'position', None))
            if camera_position is None:
                camera_position = reference_start
            shot_id = self._next_shot_id
            self._next_shot_id += 1
            # Exact #1513 ABI:
            # add(id, effects, gravity, refStart, refVelocity, start,
            #     maxDistance, attackerID, tracerCameraPos)
            mover.add(
                shot_id, effects_descr, gravity, reference_start,
                reference_velocity, visual_start, maximum, attacker_id,
                camera_position)
            # ``ProjectileMover.add`` has no return-value contract.  In
            # #1513 it silently returns without creating a projectile during
            # replay time-warp and when the native ballistics simulator
            # rejects the launch.  Do not turn that failure into a permanent
            # projectile-id dedupe entry: a later authoritative snapshot must
            # be allowed to retry it.
            active = getattr(
                mover, '_ProjectileMover__projectiles', None)
            if (not artillery and
                    (not isinstance(active, dict) or shot_id not in active)):
                self._report_failure('native add rejected')
                return False
            if is_ricochet:
                hold = getattr(mover, 'hold', None)
                if not callable(hold):
                    self._hide_untracked(mover, shot_id, reference_start)
                    return False
                try:
                    hold(shot_id)
                except Exception as error:
                    self._hide_untracked(mover, shot_id, reference_start)
                    self._report_failure('native hold exception', error)
                    return False
            if projectile_id is not None:
                self._projectile_shots[projectile_id] = (
                    shot_id, artillery, attacker_id, reference_start)
                self._projectile_order.append(projectile_id)
            return shot_id
        except Exception as error:
            if mover is not None and shot_id is not None:
                self._hide_untracked(mover, shot_id, reference_start)
            self._launches_enabled = False
            self._report_failure('launch exception', error)
            return False

    def _prune_stale_projectiles(self, mover):
        """Release dedupe rows already retired by native ballistics."""
        active = getattr(mover, '_ProjectileMover__projectiles', None)
        if not isinstance(active, dict):
            return False
        changed = False
        for projectile_id, entry in tuple(self._projectile_shots.items()):
            shot_id, artillery = entry[:2]
            if not artillery and shot_id not in active:
                self._remove_projectile_mapping(projectile_id)
                changed = True
        return changed

    def _ensure_visual_capacity(self, mover, attacker_id):
        """Retire oldest visuals before native pools can grow unbounded."""
        while True:
            attacker_active = sum(
                1 for entry in self._projectile_shots.values()
                if len(entry) > 2 and entry[2] == attacker_id)
            total_active = len(self._projectile_shots)
            if (attacker_active < self._MAX_ACTIVE_PER_ATTACKER and
                    total_active < self._MAX_ACTIVE_TOTAL):
                return True
            candidate = None
            for projectile_id in tuple(self._projectile_order):
                entry = self._projectile_shots.get(projectile_id)
                if entry is None:
                    self._remove_projectile_order(projectile_id)
                    continue
                if (attacker_active >= self._MAX_ACTIVE_PER_ATTACKER and
                        len(entry) > 2 and entry[2] != attacker_id):
                    continue
                candidate = projectile_id
                break
            if candidate is None:
                return False
            if not self._retire_for_pressure(mover, candidate):
                # A failed native hide is the exact moment to stop adding
                # cosmetic work.  Authority continues without this presenter.
                self._launches_enabled = False
                return False

    def _retire_for_pressure(self, mover, projectile_id):
        entry = self._projectile_shots.get(projectile_id)
        if entry is None:
            return True
        shot_id = entry[0]
        endpoint = entry[3] if len(entry) > 3 else None
        if not self._hide_untracked(mover, shot_id, endpoint):
            return False
        attacker_id = entry[2] if len(entry) > 2 else 0
        self._remove_projectile_mapping(projectile_id)
        if attacker_id > 0:
            self._visual_admissions[projectile_id] = (attacker_id, False)
        return True

    def _hide_untracked(self, mover, shot_id, endpoint):
        callback = getattr(mover, 'hide', None)
        endpoint = self._finite_vector(endpoint)
        if not callable(callback) or endpoint is None:
            return False
        try:
            callback(shot_id, endpoint)
        except Exception as error:
            self._report_failure('pressure hide exception', error)
            return False
        return True

    def _remove_projectile_mapping(self, projectile_id):
        self._projectile_shots.pop(projectile_id, None)
        self._remove_projectile_order(projectile_id)

    def _remove_projectile_order(self, projectile_id):
        try:
            self._projectile_order.remove(projectile_id)
        except ValueError:
            pass

    def stop_canonical(self, projectile_id, end_position,
                       explosion=None):
        """Retire one authoritative tracer at its canonical terminal point.

        ``explosion`` carries ``(effectsDescr, effectMaterial, velocityDir)``
        for a terminal on the world.  Retail sends that terminal straight to
        ``ProjectileMover.explode`` so its native ballistics simulator can
        correct the impact point and schedule the material effect.  ``hide``
        deliberately clears ``showExplosion`` and is reserved for vehicle or
        effect-free terminals.  If the explosion call itself fails, hiding is
        the safe fallback so a tracer cannot remain alive indefinitely.
        """
        if self._closed or projectile_id is None:
            return False
        try:
            projectile_id = str(projectile_id)
        except Exception:
            return False
        entry = self._projectile_shots.get(projectile_id)
        end = self._finite_vector(end_position)
        if entry is None:
            # A denied or pressure-retired visual still receives the complete
            # authoritative terminal.  Forget only its cosmetic admission.
            self._visual_admissions.pop(projectile_id, None)
            return False
        if end is None:
            return False
        shot_id = entry[0]
        mover = self._mover
        if mover is None:
            return False
        if self._explode_canonical(mover, shot_id, end, explosion):
            self._remove_projectile_mapping(projectile_id)
            self._visual_admissions.pop(projectile_id, None)
            return True
        callback = getattr(mover, 'hide', None) if mover is not None else None
        if not callable(callback):
            return False
        try:
            callback(shot_id, end)
        except Exception as error:
            self._launches_enabled = False
            self._report_failure('terminal hide exception', error)
            return False
        self._remove_projectile_mapping(projectile_id)
        self._visual_admissions.pop(projectile_id, None)
        return True

    def _explode_canonical(self, mover, shot_id, end, explosion):
        """Play the retail ground explosion for one world terminal.

        Keeping the positive shot id alive is important: the retail method
        first asks ``PyBallisticsSimulator.explodeProjectile`` for its corrected
        terminal point and direction, or marks the live projectile to render
        the explosion from the native terminal callback.
        """
        if not explosion or not self._explosions_enabled:
            return False
        explode = getattr(mover, 'explode', None)
        if not callable(explode):
            return False
        try:
            effects_descr, effect_material, velocity = explosion
        except (TypeError, ValueError):
            return False
        if not effects_descr or not effect_material:
            return False
        # An artillery-strike descriptor makes ProjectileMover.explode return
        # before it plays anything; never pretend that produced an effect.
        try:
            if effects_descr.get('artilleryID') is not None:
                return False
        except AttributeError:
            return False
        direction = self._finite_vector(velocity)
        if direction is None:
            return False
        try:
            if direction.length <= 0.0:
                return False
            direction.normalise()
            explode(shot_id, effects_descr, str(effect_material), end,
                    direction)
        except Exception as error:
            self._explosions_enabled = False
            self._report_failure('terminal explosion exception', error)
            return False
        return True

    @staticmethod
    def _finite_float(value):
        try:
            value = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if math.isnan(value) or math.isinf(value):
            return None
        return value

    def _finite_vector(self, value):
        if value is None:
            return None
        try:
            components = (value.x, value.y, value.z)
        except AttributeError:
            try:
                components = (value[0], value[1], value[2])
            except (TypeError, IndexError, KeyError):
                return None
        components = tuple(self._finite_float(item) for item in components)
        if None in components:
            return None
        try:
            return self._math.Vector3(*components)
        except Exception:
            return None

    def _projectile_mover(self):
        if (self._mover is None and not self._closed and
                self._launches_enabled):
            mover = None
            try:
                from ProjectileMover import ProjectileMover
                mover = ProjectileMover()
                set_space_id = getattr(mover, 'setSpaceID', None)
                if not callable(set_space_id):
                    raise RuntimeError(
                        '#1513 ProjectileMover has no setSpaceID')
                # Stock PlayerAvatar binds the ballistics simulator to the
                # current space before its first addProjectile call.  A mover
                # without this binding can fail silently or trace against the
                # wrong collision scene.
                set_space_id(self._space_id)
                self._mover = mover
            except Exception as error:
                destroy = getattr(mover, 'destroy', None)
                if callable(destroy):
                    try:
                        destroy()
                    except Exception:
                        pass
                self._launches_enabled = False
                self._report_failure('mover setup', error)
                return None
        return self._mover

    def _report_failure(self, stage, error=None):
        """Log each native tracer failure stage once per battle."""
        stage = str(stage)
        if stage in self._failure_stages:
            return False
        self._failure_stages.add(stage)
        detail = '' if error is None else ': %s' % error
        sys.stdout.write(
            '[Offline LAN 0.9.22] projectile visual %s%s\n' % (
                stage, detail))
        return True

    def _muzzle_position(self, vehicle):
        try:
            node = vehicle.model.node('HP_gunFire')
            return self._math.Vector3(self._math.Matrix(node).translation)
        except Exception:
            position = self._math.Vector3(vehicle.position)
            position.y += 1.5
            return position

    def _direction(self, vehicle):
        # The regular aim matrix uses negative pitch to raise the barrel.
        # A bot_shot event can temporarily provide the already-dispersed
        # physical ray, whose pitch is positive upward.  The tracer and
        # authority collision resolver must consume that same ray.
        shot_pitch = getattr(vehicle, '_offlineLANShotPitch', None)
        if shot_pitch is None:
            pitch = -float(getattr(vehicle, '_gun_pitch', 0.0) or 0.0)
        else:
            pitch = float(shot_pitch)
        yaw = float(getattr(
            vehicle, '_offlineLANShotYaw',
            getattr(vehicle, '_aim_yaw', vehicle.yaw)) or 0.0)
        horizontal = math.cos(pitch)
        direction = self._math.Vector3(
            math.sin(yaw) * horizontal, math.sin(pitch),
            math.cos(yaw) * horizontal)
        direction.normalise()
        return direction

    @staticmethod
    def _active_shot(vehicle):
        descriptor = vehicle.typeDescriptor
        gun = getattr(descriptor, 'gun', None)
        shots = tuple(_component_value(gun, 'shots', ()) or ())
        if not shots:
            return None
        try:
            index = int(getattr(
                vehicle, '_offlineLANShotIndex',
                getattr(descriptor, 'activeGunShotIndex', 0)) or 0)
        except (TypeError, ValueError):
            index = 0
        return shots[max(0, min(index, len(shots) - 1))]

    @staticmethod
    def _shot_at(descriptor, shell_index):
        gun = getattr(descriptor, 'gun', None)
        shots = tuple(_component_value(gun, 'shots', ()) or ())
        if not shots:
            return None
        try:
            index = int(shell_index)
        except (TypeError, ValueError, OverflowError):
            return None
        if index < 0 or index >= len(shots):
            return None
        return shots[index]

    def destroy(self):
        self._closed = True
        mover = self._mover
        if mover is not None:
            callback = getattr(mover, 'destroy', None)
            if not callable(callback):
                raise RuntimeError(
                    '#1513 ProjectileMover has no destroy lifecycle')
            callback()
        # Keep the mover and its projectile ownership intact when native
        # teardown raises.  ``destroy_all`` may then retry the same object
        # instead of leaking a callback subscription with no Python owner.
        self._mover = None
        self._projectile_shots = {}
        self._projectile_order = []
        self._visual_budgets = {}
        self._visual_admissions = {}


class _RemoteFilter(object):

    # ProjectileMover only uses this as a broad-phase rejection before asking
    # RemoteVehicle.collideSegment for the descriptor hit-test.  Twenty metres
    # encloses every #1513 tank, including its gun, without making the precise
    # collision result less authoritative.
    _BROAD_PHASE_RADIUS_SQUARED = 20.0 * 20.0

    def __init__(self, math_module, position, matrix_provider):
        self._math = math_module
        self.position = math_module.Vector3(position)
        self.velocity = math_module.Vector3(0.0, 0.0, 0.0)
        self.speed = 0.0
        # SteadyVehicleMatrixCalculator.relinkSources reads these exact
        # WGVehicleFilter providers whenever Avatar.vehicle changes.  A LAN
        # RemoteVehicle owns one in-place Math.Matrix instead of a native
        # filter, so both stock aiming paths must follow that same provider.
        self.stabilisedMatrix = matrix_provider
        self.groundPlacingMatrixFiltered = matrix_provider

    def update(self, position, velocity):
        self.position = self._math.Vector3(position)
        self.velocity = self._math.Vector3(velocity)
        try:
            self.speed = float(self.velocity.length)
        except Exception:
            self.speed = 0.0

    def segmentMayHitEntity(self, startPoint, endPoint, skipGun):
        """Implement the exact three-argument #1513 vehicle-filter ABI."""
        unused_skip_gun = skipGun
        dx = float(endPoint.x) - float(startPoint.x)
        dy = float(endPoint.y) - float(startPoint.y)
        dz = float(endPoint.z) - float(startPoint.z)
        px = float(self.position.x) - float(startPoint.x)
        py = float(self.position.y) - float(startPoint.y)
        pz = float(self.position.z) - float(startPoint.z)
        length_squared = dx * dx + dy * dy + dz * dz
        if length_squared <= 1e-9:
            fraction = 0.0
        else:
            fraction = (px * dx + py * dy + pz * dz) / length_squared
            fraction = max(0.0, min(1.0, fraction))
        offset_x = px - dx * fraction
        offset_y = py - dy * fraction
        offset_z = pz - dz * fraction
        return (offset_x * offset_x + offset_y * offset_y +
                offset_z * offset_z <= self._BROAD_PHASE_RADIUS_SQUARED)


class _RemoteSpeedInfo(object):
    """Expose the value tuple consumed by #1513's observed-vehicle UI."""

    def __init__(self, vehicle_filter):
        self._filter = vehicle_filter

    @property
    def value(self):
        return (self._filter.speed, 0.0)


def _component_value(component, name, default=None):
    if isinstance(component, dict):
        return component.get(name, default)
    return getattr(component, name, default)


def _component_aim_angles(descriptor, turret_yaw, gun_pitch):
    """Apply #1513's installed-gun constants to component matrices only."""
    gun = _component_value(descriptor, 'gun')
    static_yaw = _component_value(gun, 'staticTurretYaw')
    static_pitch = _component_value(gun, 'staticPitch')
    if static_yaw is not None:
        turret_yaw = static_yaw
    if static_pitch is not None:
        gun_pitch = static_pitch
    return float(turret_yaw), float(gun_pitch)


def _pose_components(vehicle, math_module):
    """Build descriptor-local hit-test transforms below the body pose."""
    descriptor = vehicle.typeDescriptor
    result = []
    identity = math_module.Matrix()
    identity.setIdentity()
    result.append((descriptor.chassis, identity))

    hull_offset = _component_value(
        descriptor.chassis, 'hullPosition',
        math_module.Vector3(0.0, 0.0, 0.0))
    hull = math_module.Matrix()
    hull.setTranslate(-hull_offset)
    result.append((descriptor.hull, hull))

    turret_positions = _component_value(
        descriptor.hull, 'turretPositions', ())
    turret_offset = (turret_positions[0] if turret_positions else
                     math_module.Vector3(0.0, 0.0, 0.0))
    turret = math_module.Matrix()
    turret.setTranslate(-hull_offset - turret_offset)
    rotation = math_module.Matrix()
    turret_yaw = math_module.Matrix(vehicle.appearance.turretMatrix).yaw
    rotation.setRotateY(-turret_yaw)
    turret.postMultiply(rotation)
    result.append((descriptor.turret, turret))

    gun_offset = _component_value(
        descriptor.turret, 'gunPosition',
        math_module.Vector3(0.0, 0.0, 0.0))
    gun = math_module.Matrix()
    gun.setTranslate(-gun_offset)
    rotation = math_module.Matrix()
    gun_pitch = math_module.Matrix(vehicle.appearance.gunMatrix).pitch
    rotation.setRotateX(-gun_pitch)
    gun.postMultiply(rotation)
    gun.preMultiply(turret)
    result.append((descriptor.gun, gun))
    return result


def _damage_sticker_coordinate(value, lower, upper):
    """Quantize one #1513 damage-sticker coordinate to its wire byte."""
    value = float(value)
    lower = float(lower)
    upper = float(upper)
    if (math.isnan(value) or math.isinf(value) or
            math.isnan(lower) or math.isinf(lower) or
            math.isnan(upper) or math.isinf(upper) or upper <= lower):
        raise ValueError('damage-sticker bounds are invalid')
    ratio = max(0.0, min(1.0, (value - lower) / (upper - lower)))
    return int(round(ratio * 255.0))


def _clip_damage_sticker_segment(start_point, end_point, bbox):
    """Clip a component-local ray to its bbox before byte quantization."""
    start = tuple(float(start_point[axis]) for axis in range(3))
    end = tuple(float(end_point[axis]) for axis in range(3))
    delta = tuple(end[axis] - start[axis] for axis in range(3))
    minimum = tuple(float(bbox[0][axis]) for axis in range(3))
    maximum = tuple(float(bbox[1][axis]) for axis in range(3))
    entry = 0.0
    exit = 1.0
    for axis in range(3):
        if maximum[axis] <= minimum[axis]:
            return None
        if abs(delta[axis]) <= 1.0e-12:
            if start[axis] < minimum[axis] or start[axis] > maximum[axis]:
                return None
            continue
        first = (minimum[axis] - start[axis]) / delta[axis]
        second = (maximum[axis] - start[axis]) / delta[axis]
        if first > second:
            first, second = second, first
        entry = max(entry, first)
        exit = min(exit, second)
        if exit < entry:
            return None
    return (
        tuple(start[axis] + delta[axis] * entry for axis in range(3)),
        tuple(start[axis] + delta[axis] * exit for axis in range(3)),
    )


def encode_damage_sticker(vehicle, vehicle_matrix, start_point, end_point,
                          component_name, sticker_id, math_module,
                          chassis_matrix=None):
    """Encode the exact uint64 consumed by #1513 DamageFromShotDecoder.

    The authoritative projectile query is already expressed at the sampled
    target pose. Preserve that pose here instead of reconstructing the mark
    later from a replica's current model matrices.
    """
    if (vehicle is None or vehicle_matrix is None or
            not isinstance(component_name, _STRING_TYPES) or
            isinstance(sticker_id, bool)):
        return None
    try:
        sticker_id = int(sticker_id)
    except (TypeError, ValueError, OverflowError):
        return None
    if not 0 <= sticker_id <= 255:
        return None
    try:
        components = _pose_components(vehicle, math_module)
        for component_index, pair in enumerate(components):
            component, component_matrix = pair
            if _component_value(component, 'itemTypeName') != component_name:
                continue
            tester = _component_value(component, 'hitTester')
            bbox = _component_value(tester, 'bbox')
            if bbox is None:
                return None
            root_matrix = (chassis_matrix if component_index == 0 and
                           chassis_matrix is not None else vehicle_matrix)
            world_to_root = math_module.Matrix(root_matrix)
            world_to_root.invert()
            local_points = (
                component_matrix.applyPoint(
                    world_to_root.applyPoint(start_point)),
                component_matrix.applyPoint(
                    world_to_root.applyPoint(end_point)),
            )
            local_points = _clip_damage_sticker_segment(
                local_points[0], local_points[1], bbox)
            if local_points is None:
                return None
            coordinate_shifts = ((16, 24, 32), (40, 48, 56))
            encoded = sticker_id | (component_index << 8)
            quantized = []
            for point, shifts in zip(local_points, coordinate_shifts):
                point_bytes = []
                for axis, shift in enumerate(shifts):
                    coordinate = _damage_sticker_coordinate(
                        point[axis], bbox[0][axis], bbox[1][axis])
                    point_bytes.append(coordinate)
                    encoded |= coordinate << shift
                quantized.append(tuple(point_bytes))
            if quantized[0] == quantized[1]:
                return None
            return encoded
    except (AttributeError, IndexError, TypeError, ValueError,
            OverflowError):
        return None
    return None


def _collide_vehicle_at_matrix(vehicle, vehicle_matrix, start_point,
                               end_point, math_module, include_world_normal,
                               chassis_matrix=None):
    """Run precise descriptor collision at supplied body/chassis matrices.

    #1513's native ``Vehicle.collideSegmentExt`` first rejects rays through
    the retail ``WGVehicleFilter``. Copied 0.8.2 physics deliberately leaves
    that filter at the spawn pose, so local incoming shots must use the live
    presentation matrix instead. Remote vehicles use the same routine to
    keep outgoing and incoming collision geometry identical. Hydraulic
    vehicles use ``bodyMatrix`` for hull/turret/gun and the separate
    ``groundPlacingMatrix`` for chassis, matching stock ``getComponents``.
    """
    vehicle_to_world = None
    chassis_to_world = None
    if include_world_normal:
        vehicle_to_world = math_module.Matrix(vehicle_matrix)
        chassis_to_world = vehicle_to_world
    world_to_vehicle = math_module.Matrix(vehicle_matrix)
    world_to_vehicle.invert()
    body_start = world_to_vehicle.applyPoint(start_point)
    body_end = world_to_vehicle.applyPoint(end_point)
    chassis_start = body_start
    chassis_end = body_end
    if chassis_matrix is not None and chassis_matrix is not vehicle_matrix:
        if include_world_normal:
            chassis_to_world = math_module.Matrix(chassis_matrix)
        world_to_chassis = math_module.Matrix(chassis_matrix)
        world_to_chassis.invert()
        chassis_start = world_to_chassis.applyPoint(start_point)
        chassis_end = world_to_chassis.applyPoint(end_point)
    hits = []
    for component_index, pair in enumerate(_pose_components(
            vehicle, math_module)):
        component, component_matrix = pair
        tester = _component_value(component, 'hitTester')
        local_hit_test = getattr(tester, 'localHitTest', None)
        if not callable(local_hit_test):
            continue
        start = chassis_start if component_index == 0 else body_start
        end = chassis_end if component_index == 0 else body_end
        collisions = local_hit_test(
            component_matrix.applyPoint(start),
            component_matrix.applyPoint(end))
        component_to_vehicle = None
        if include_world_normal:
            component_to_vehicle = math_module.Matrix(component_matrix)
            component_to_vehicle.invert()
        for collision in collisions or ():
            try:
                dist, local_normal, angle_cos, material_kind = collision
            except (TypeError, ValueError):
                continue
            materials = _component_value(component, 'materials', {}) or {}
            material = materials.get(material_kind)
            component_name = _component_value(component, 'itemTypeName')
            if not isinstance(component_name, _STRING_TYPES):
                raise RuntimeError(
                    '#1513 collision component has no itemTypeName')
            result = _SegmentCollisionResultExt(
                float(dist), float(angle_cos), material, component_name)
            if include_world_normal:
                world_normal = None
                if local_normal is not None:
                    vehicle_normal = component_to_vehicle.applyVector(
                        local_normal)
                    root_to_world = (chassis_to_world
                                     if component_index == 0 else
                                     vehicle_to_world)
                    world_normal = root_to_world.applyVector(
                        vehicle_normal)
                    world_normal = math_module.Vector3(world_normal)
                    if world_normal.length > 0.0:
                        world_normal.normalise()
                    else:
                        world_normal = None
                result = _VehicleCollisionEvidence(result, world_normal)
            hits.append(result)
    if include_world_normal:
        hits.sort(key=lambda item: item.collision.dist)
    else:
        hits.sort(key=lambda item: item.dist)
    return hits


def _collide_vehicle_evidence_at_matrix(vehicle, vehicle_matrix, start_point,
                                        end_point, math_module,
                                        chassis_matrix=None):
    """Return world-normal evidence for the authoritative shot resolver."""
    return _collide_vehicle_at_matrix(
        vehicle, vehicle_matrix, start_point, end_point, math_module, True,
        chassis_matrix=chassis_matrix)


def collide_vehicle_at_matrix(vehicle, vehicle_matrix, start_point,
                              end_point, math_module, chassis_matrix=None):
    """Return #1513's exact four-field collision values for public callers."""
    return _collide_vehicle_at_matrix(
        vehicle, vehicle_matrix, start_point, end_point, math_module, False,
        chassis_matrix=chassis_matrix)


class RemoteVehicle(object):
    """Python gameplay identity separated from its OfflineEntity visual."""

    _offlineLANPresentation = True

    def __init__(self, entity_id, descriptor, properties, position, rotation,
                 math_module, shot_presenter=None):
        self.id = int(entity_id)
        self.typeDescriptor = descriptor
        self.vehicleTypeDescriptor = descriptor
        self.publicInfo = dict(properties.get('publicInfo') or {})
        self.team = int(self.publicInfo.get('team', 0) or 0)
        self.health = int(properties.get('health', descriptor.maxHealth))
        self.maxHealth = int(descriptor.maxHealth)
        self.isCrewActive = bool(properties.get('isCrewActive', True))
        self.isAlive = _AliveFlag(self.health > 0 and self.isCrewActive)
        self.isPlayerVehicle = False
        self.isStarted = False
        self.inWorld = False
        self.isObserver = False
        self.isStrafing = False
        self.steeringAngle = 0.0
        self.gunAnglesPacked = int(properties.get('gunAnglesPacked', 0))
        self.physicsMode = properties.get('physicsMode', 0)
        self.siegeState = properties.get('siegeState', 0)
        self.engineMode = properties.get('engineMode', (0, 0))
        self.damageStickers = properties.get('damageStickers', [])
        self.publicStateModifiers = properties.get(
            'publicStateModifiers', ())
        self.stunInfo = properties.get('stunInfo', 0.0)
        self.last_killer_id = 0
        self.last_shot = None
        self.last_shot_effect = None
        self.model = None
        self.bw_entity = None
        self.bw_entity_id = None
        self.load_error = None
        self._math = math_module
        self._shot_presenter = shot_presenter
        self._gun_recoil = None
        self._collision_obstacle = None
        self.track_filter = None
        self.track_scroll = None
        self.track_flying_info = None
        self.fashions = None
        self._track_mode = None
        self._offlineLANShotIndex = int(
            getattr(descriptor, 'activeGunShotIndex', 0) or 0)
        # Stock BigWorld.entity()/entities are presentation/AOI lookups. The
        # LAN authority uses RemoteVehicleFactory.get() explicitly, so an
        # unspotted enemy never leaks into native aiming or ProjectileMover.
        self._spot_visible = False
        self._postmortem_visible = False
        # helpers.EntityExtra stores each running stock extra on the entity.
        # Without this dictionary the #1513 shoot extra cannot start.
        self.extras = {}
        self.position = math_module.Vector3(position)
        self.yaw = float(rotation[2])
        self.pitch = float(rotation[1])
        self.roll = float(rotation[0])
        self.matrix = math_module.Matrix()
        # The animation and its two keyframe matrices belong to this vehicle
        # for its whole life; rekeying rewrites their contents in place.
        self._animation = None
        self._key_from = math_module.Matrix()
        self._key_to = math_module.Matrix()
        global _pose_object_allocations
        _pose_object_allocations += 2
        self._render_pose = None
        self._render_from = None
        self._render_to = None
        self._render_started = 0.0
        self._render_duration = 0.0
        self.filter = _RemoteFilter(math_module, self.position, self.matrix)
        self.speedInfo = _RemoteSpeedInfo(self.filter)
        self.appearance = _RemoteAppearance(math_module, self)
        self.proxy = weakref.proxy(self)
        self._aim_yaw = self.yaw
        self._gun_pitch = 0.0
        self._offline_outfit = None
        self._offline_outfit_valid = None
        self._vehicle_stickers = None
        self._last_pose_time = None
        self._wreck_retained = False
        self._update_matrix()

    def _update_matrix(self):
        self.matrix.setRotateYPR((self.yaw, self.pitch, self.roll))
        self.matrix.translation = self.position

    def attach_visual(self, entity, entity_id, model):
        self.bw_entity = entity
        self.bw_entity_id = int(entity_id)
        self.model = model
        # PyCompoundModel has no ordinary Model position/yaw/pitch/roll
        # attributes.  Exact #1513 CompoundAppearance links the compound to
        # the vehicle's live matrix provider and then mutates that provider.
        #
        # A bot has no WGVehicleFilter to interpolate for it: OfflineEntity
        # declares an empty <Volatile/> block, so the engine never delivers a
        # motion sample and filter.movementInfo would stay at rest.  Drive the
        # compound through Math.MatrixAnimation instead, which is the same
        # provider gun_marker_ctrl._updateMatrixProvider uses to smooth a
        # server-tick-rate update up to the render rate.
        self._animation = self._new_animation()
        self.model.matrix = (
            self._animation if self._animation is not None else self.matrix)
        self.appearance.attach(model)
        if self._shot_presenter is not None:
            self._shot_presenter.setup_turret_rotations(self)
            self._shot_presenter.setup_recoil(self)
        self.set_pose(self.position, (self.roll, self.pitch, self.yaw))
        self.isStarted = True
        self.inWorld = True

    def attach_track_animation(self, vehicle_filter, scroll, fashions,
                               flying_info=None):
        """Adopt the native belt animation assembled for this bot."""
        self.track_filter = vehicle_filter
        self.track_scroll = scroll
        self.track_flying_info = flying_info
        self.fashions = fashions
        self._track_mode = None
        return True

    def attach_stickers(self, stickers):
        self._vehicle_stickers = stickers
        return True

    def _release_stickers(self, engine_alive=True):
        stickers = self._vehicle_stickers
        if stickers is not None and engine_alive:
            stickers.detach()
        if self._vehicle_stickers is stickers:
            self._vehicle_stickers = None
        return stickers is not None

    def update_tracks(self, left, right, mode):
        """Feed one frame of belt speed through the stock PyTrackScroll path.

        ``PyTrackScroll`` owns the filter's belt-speed override, because its
        own 20 Hz updater writes those fields every tick.  ``setMode`` and
        ``setExternal`` are the only sanctioned writers, the same pair
        #1513 uses in ``CompoundAppearance.changeEngineMode`` and
        ``updateTracksScroll``.
        """
        scroll = self.track_scroll
        if scroll is None:
            return False
        if mode != self._track_mode:
            scroll.setMode(mode)
            self._track_mode = mode
        scroll.setExternal(float(left), float(right))
        return True

    def track_scroll_readback(self):
        """Call the four #1513 ``PyTrackScroll`` readers and return their values."""
        scroll = self.track_scroll
        if scroll is None:
            return None
        values = []
        for name in ('leftScroll', 'rightScroll', 'leftContact',
                     'rightContact'):
            reader = getattr(scroll, name, None)
            if callable(reader):
                try:
                    values.append(reader())
                    continue
                except Exception as error:
                    values.append('%s!%s' % (name, error))
                    continue
            values.append(reader)
        return tuple(values)

    def _release_track_animation(self, engine_alive=True):
        """Retire the scroll controller before the filter reference goes.

        ``setData`` keeps a raw, non-owning pointer to the native filter and
        ``activate`` registers a 20 Hz updater that dereferences it, so the
        controller must be deactivated and cleared while the filter is still
        alive.  Once the engine has torn the space down, drop both references
        without calling into either object.
        """
        scroll = self.track_scroll
        released = scroll is not None
        if released and engine_alive:
            scroll.deactivate()
            scroll.setData(None)
        self.track_scroll = None
        self.track_filter = None
        self.track_flying_info = None
        self.fashions = None
        self._track_mode = None
        return released

    def retain_wreck_model(self):
        """Freeze the loaded compound as non-blocking wreck cover.

        ``loadResourceListBG`` still finalizes a newly assembled destroyed
        compound on BigWorld's callback thread.  Exact client logs show that
        cold finalization can stop every callback for 9--15 seconds.  A
        second compound per vehicle would avoid the cold load only by roughly
        doubling the model ownership of this 32-bit client, so remote wrecks
        keep their existing compound and the independent dead marker instead.
        Descriptor hit testers already own gameplay collision.
        """
        if self.model is None or self._wreck_retained:
            return False
        self._wreck_retained = True
        self._stop_extras()
        self.appearance.engineAudition.detach()
        effects = self.appearance._bound_effects
        if effects is not None:
            try:
                effects.destroy()
            except Exception:
                pass
            else:
                if self.appearance._bound_effects is effects:
                    self.appearance._bound_effects = None
        self.engineMode = (0, 0)
        self.appearance.gear = 0
        self.filter.update(
            self.position, self._math.Vector3(0.0, 0.0, 0.0))
        try:
            self.update_tracks(0.0, 0.0, self.engineMode)
        except Exception:
            pass
        sys.stdout.write(
            '[Offline LAN 0.9.22] WRECK retained id=%s '
            'pose=(%.1f, %.1f, %.1f)\n' % (
                self.bw_entity_id, self.position.x, self.position.y,
                self.position.z))
        return True

    def attach_wreck_model(self, model):
        """Swap this vehicle onto its loaded #1513 destroyed compound."""
        sys.stdout.write(
            '[Offline LAN 0.9.22] WRECK swap id=%s pose=(%.1f, %.1f, %.1f)\n'
            % (self.bw_entity_id, self.position.x, self.position.y,
               self.position.z))
        self._stop_extras()
        self._release_stickers()
        previous = self.model
        self.appearance.detach()
        self.appearance.gunRecoil = None
        self._gun_recoil = None
        # CompoundAppearance.deactivate detaches the compound from the entity
        # before it severs the live matrix provider, and __linkCompound clears
        # entity.model before it attaches the replacement.
        self.bw_entity.model = None
        if previous is not None:
            previous.matrix = self._math.Matrix()
        self.bw_entity.model = model
        self.model = model
        self.model.matrix = self.matrix
        self.appearance.attach(model)
        self.appearance.damageState.isCurrentModelDamaged = True
        if self._shot_presenter is not None:
            self._shot_presenter.setup_turret_rotations(self)
        return True

    def detach_visual(self):
        self._stop_extras()
        self._release_stickers()
        self._release_track_animation()
        model = self.model
        entity = self.bw_entity
        if entity is not None:
            entity.model = None
        if model is not None:
            # Match CompoundAppearance.deactivate(): the compound leaves
            # the entity first, and only then loses the live provider.
            model.matrix = self._math.Matrix()
        self.appearance.detach()
        self._collision_obstacle = None
        self.isStarted = False
        self.inWorld = False
        self.appearance.gunRecoil = None
        self.bw_entity = None
        self.bw_entity_id = None
        self.model = None
        self._gun_recoil = None

    def abandon_visual(self):
        """Forget every native object without touching one of them.

        BigWorld resets the entity manager and clears every space before
        ``guiModsFini`` reaches this mod, so by then the compound, the filter
        and the scroll controller this vehicle points at are already freed.
        """
        self._release_track_animation(engine_alive=False)
        self._release_stickers(engine_alive=False)
        self.extras.clear()
        self._collision_obstacle = None
        self.isStarted = False
        self.inWorld = False
        self.appearance.abandon()
        self.bw_entity = None
        self.bw_entity_id = None
        self.model = None
        self._animation = None
        self._gun_recoil = None
        return True

    def _new_animation(self):
        """Create this vehicle's one animation, keyed to its own matrices."""
        factory = getattr(self._math, 'MatrixAnimation', None)
        if not callable(factory):
            return None
        try:
            animation = factory()
        except Exception:
            return None
        global _pose_object_allocations
        _pose_object_allocations += 1
        pose = (float(self.position.x), float(self.position.y),
                float(self.position.z), self.yaw, self.pitch, self.roll)
        self._write_pose(self._key_from, pose)
        self._write_pose(self._key_to, pose)
        self._render_pose = pose
        try:
            animation.keyframes = ((0.0, self._key_from),)
            animation.time = 0.0
        except Exception:
            return None
        return animation

    def _mirror_pose(self, now):
        """Return where the compound is drawn right now.

        The engine interpolates the keyframes linearly from time 0, so the
        same linear blend reproduces the drawn pose without reading back a
        native provider.
        """
        target = self._render_to
        if target is None:
            return self._render_pose
        source = self._render_from
        if source is None or now is None or self._render_duration <= 0.0:
            return target
        ratio = (float(now) - self._render_started) / self._render_duration
        if ratio >= 1.0:
            return target
        ratio = max(0.0, ratio)
        return (
            source[0] + (target[0] - source[0]) * ratio,
            source[1] + (target[1] - source[1]) * ratio,
            source[2] + (target[2] - source[2]) * ratio,
            _blend_angle(source[3], target[3], ratio),
            _blend_angle(source[4], target[4], ratio),
            _blend_angle(source[5], target[5], ratio))

    def _retarget_render_pose(self, relax_time, now):
        """Rekey the compound's animation from where it is to the new pose.

        The two keyframe matrices belong to this vehicle for its whole life;
        only their contents change, so a battle never allocates a new native
        pose object after the vehicle exists.
        """
        target = (float(self.position.x), float(self.position.y),
                  float(self.position.z), self.yaw, self.pitch, self.roll)
        relax_time = float(relax_time or 0.0)
        animation = self._animation
        if animation is None:
            self._render_pose = target
            self._write_pose(self._key_to, target)
            return False
        current = self._mirror_pose(now)
        if relax_time <= 0.0 or current is None:
            self._render_from = None
            self._render_to = None
            self._render_pose = target
            self._write_pose(self._key_from, target)
            self._write_pose(self._key_to, target)
            return self._rekey(0.0)
        if current == target:
            return False
        self._write_pose(self._key_from, current)
        self._write_pose(self._key_to, target)
        self._render_from = current
        self._render_to = target
        self._render_pose = current
        self._render_started = float(now or 0.0)
        self._render_duration = relax_time
        return self._rekey(relax_time)

    def _rekey(self, relax_time):
        """Point the animation at this vehicle's own two keyframe matrices.

        Both stock users key the second frame at a strictly positive time.
        Two keys at the same time leave the native blend factor at zero over
        zero, which turns the whole compound's transform into NaN.
        """
        try:
            self._animation.keyframes = (
                (0.0, self._key_from),
                (max(relax_time, _MINIMUM_KEYFRAME_SECONDS), self._key_to))
            self._animation.time = 0.0
        except Exception:
            self._animation = None
            if self.model is not None:
                self.model.matrix = self.matrix
            return False
        return True

    @staticmethod
    def _write_pose(matrix, pose):
        """Write one pose into an existing native matrix, in place."""
        matrix.setRotateYPR((pose[3], pose[4], pose[5]))
        matrix.translation = (pose[0], pose[1], pose[2])
        return True

    def set_pose(self, position, rotation, relax_time=None, now=None):
        previous = self.position
        previous_time = self._last_pose_time
        self.position = self._math.Vector3(position)
        self.roll = float(rotation[0])
        self.pitch = float(rotation[1])
        self.yaw = float(rotation[2])
        self._update_matrix()
        self._retarget_render_pose(relax_time, now)
        velocity = self._math.Vector3(0.0, 0.0, 0.0)
        if now is not None:
            now = float(now)
            if previous_time is not None and now > previous_time:
                velocity = (self.position - previous).scale(
                    1.0 / (now - previous_time))
            self._last_pose_time = now
        # Calls without a timestamp cannot establish velocity units. Publish
        # a truthful stationary sample instead of treating displacement per
        # network update as metres per second.
        self.filter.update(self.position, velocity)
        self.appearance.gear = 1 if self.filter.speed > 0.01 else 0

    def settle_motion(self, now=None):
        """Clear pose-derived motion without re-keying the hull animation."""
        if now is not None:
            self._last_pose_time = float(now)
        velocity = self._math.Vector3(0.0, 0.0, 0.0)
        self.filter.update(self.position, velocity)
        self.appearance.gear = 0
        return True

    def set_aim(self, hull_yaw, aim_yaw, gun_pitch):
        relative = ((float(aim_yaw) - float(hull_yaw) + math.pi) %
                    (2.0 * math.pi) - math.pi)
        self._aim_yaw = float(aim_yaw)
        self._gun_pitch = float(gun_pitch)
        component_yaw, component_pitch = _component_aim_angles(
            self.typeDescriptor, relative, self._gun_pitch)
        self.appearance.turretMatrix.setRotateYPR((
            component_yaw, 0.0, 0.0))
        self.appearance.gunMatrix.setRotateYPR(
            (0.0, component_pitch, 0.0))

    def set_health(self, previous):
        self.isAlive.value = self.health > 0 and self.isCrewActive

    def set_isCrewActive(self, previous):
        self.isAlive.value = self.health > 0 and self.isCrewActive

    def onHealthChanged(self, health, attacker_id=0, reason_id=0):
        self.health = int(health)
        self.last_killer_id = int(attacker_id or 0)
        self.isAlive.value = self.health > 0 and self.isCrewActive

    def set_gunAnglesPacked(self, unused_previous):
        return None

    def onSiegeStateUpdated(self, new_state, time_to_next_mode):
        """Consume the same state edge as the exact #1513 Vehicle entity."""
        if not bool(getattr(self.typeDescriptor, 'hasSiegeMode', False)):
            return False
        callback = getattr(
            self.typeDescriptor, 'onSiegeStateChanged', None)
        if not callable(callback):
            raise RuntimeError(
                '#1513 Siege descriptor has no state-change callback')
        callback(int(new_state))
        self.appearance.onSiegeStateChanged(int(new_state))
        self.siegeState = int(new_state)
        return True

    def showShooting(self, burst_count=1, is_predicted=False):
        if (not self.isStarted or not self.inWorld or self.model is None or
                not self.isAlive() or self.siegeState not in (0, 2)):
            return False
        self.last_shot = (int(burst_count), bool(is_predicted))
        native_started = self._start_shooting_effect(max(1, int(burst_count)))
        tracer_started = bool(
            self._shot_presenter is not None and
            self._shot_presenter.play_tracer(self))
        self.last_shot_effect = (native_started, tracer_started)
        return native_started or tracer_started

    def _shoot_extra(self):
        extras = getattr(self.typeDescriptor, 'extrasDict', None)
        if extras is None:
            return None
        try:
            return extras.get('shoot')
        except AttributeError:
            try:
                return extras['shoot']
            except (KeyError, TypeError):
                return None

    def _start_shooting_effect(self, burst_count):
        extra = self._shoot_extra()
        if extra is None:
            return False
        extra.stopFor(self)
        extra.startFor(self, int(burst_count))
        return True

    def _stop_extras(self):
        """Drain every running extra through its own #1513 cleanup."""
        extra_types = getattr(self.typeDescriptor, 'extras', None) or ()
        for index, data in tuple(self.extras.items()):
            try:
                extra_types[index].stop(data)
            except Exception:
                pass
        self.extras.clear()

    def showAmmoBayEffect(self, *unused_args):
        return None

    def getSpeed(self):
        return float(self.filter.speed)

    def getAutorotation(self):
        return False

    def getComponents(self):
        return _pose_components(self, self._math)

    def collideSegmentExt(self, start_point, end_point):
        return collide_vehicle_at_matrix(
            self, self.matrix, start_point, end_point, self._math)

    def collideSegment(self, start_point, end_point, skipGun=False):
        hits = self.collideSegmentExt(start_point, end_point)
        if skipGun:
            hits = [item for item in hits
                    if item.compName != 'vehicleGun']
        if not hits:
            return None
        closest = hits[0]
        armor = getattr(closest.matInfo, 'armor', 0)
        return _SegmentCollisionResult(
            closest.dist, closest.hitAngleCos, armor)


class Vehicle(RemoteVehicle):
    """#1513 ProjectileMover ABI identity for remote presentations.

    EntityCollisionData classifies vehicles by the exact Python class name.
    Keeping the adapter as a real subclass preserves our private registry API
    while exposing the stock collision identity expected by gun markers.
    """

    pass


def _native_visible(vehicle):
    if vehicle is None:
        return False
    alive = getattr(vehicle, 'isAlive', None)
    alive = alive() if callable(alive) else bool(alive)
    return bool(
        getattr(vehicle, '_spot_visible', False) and
        getattr(vehicle, 'isStarted', False) and
        getattr(vehicle, 'inWorld', False) and
        getattr(vehicle, 'model', None) is not None and alive)


def _postmortem_visible(vehicle):
    if vehicle is None:
        return False
    alive = getattr(vehicle, 'isAlive', None)
    alive = alive() if callable(alive) else bool(alive)
    return bool(
        getattr(vehicle, '_postmortem_visible', False) and
        getattr(vehicle, 'isStarted', False) and
        getattr(vehicle, 'inWorld', False) and
        getattr(vehicle, 'model', None) is not None and alive)


class _EntitiesView(object):

    def __init__(self, original, registry):
        self._original = original
        self._registry = registry

    def __getitem__(self, key):
        try:
            return self._original[key]
        except KeyError:
            vehicle = self._registry.get(key)
            if (_native_visible(vehicle) or
                    _postmortem_visible(vehicle)):
                return vehicle
            raise

    def __setitem__(self, key, value):
        self._original[key] = value

    def __delitem__(self, key):
        del self._original[key]

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key):
        # The wrapped #1513 PyEntities answers a lookup, never a membership
        # test.
        if self._original.get(key) is not None:
            return True
        vehicle = self._registry.get(key)
        return (_native_visible(vehicle) or
                _postmortem_visible(vehicle))

    def keys(self):
        result = list(self._original.keys())
        # PostMortemControlMode.__changeVehicle checks ``entities.keys()``
        # before publishing its camera-change event.  Expose only the one
        # runtime-validated observed ally; ordinary AOI enumeration remains
        # the native registry and does not leak other synthetic identities.
        for entity_id, vehicle in self._registry.items():
            if (_postmortem_visible(vehicle) and
                    self._original.get(entity_id) is None):
                result.append(entity_id)
        return result

    def values(self):
        return self._original.values()

    def items(self):
        return self._original.items()

    def iteritems(self):
        return self._original.iteritems()

    def itervalues(self):
        return self._original.itervalues()

    def __iter__(self):
        return iter(self._original)

    def __len__(self):
        return len(self._original)

    def __getattr__(self, name):
        return getattr(self._original, name)


class RemoteVehicleFactory(object):
    """Load, register and destroy authoritative remote presentations."""

    def __init__(self, bigworld, math_module, model_assembler, space_id,
                 camouflages=None, vehicular=None, data_links=None,
                 enable_track_animation=True, outfit_factory=None,
                 vehicle_stickers_factory=None,
                 prewarm_wreck_resources=False):
        self._bigworld = bigworld
        self._math = math_module
        self._model_assembler = model_assembler
        self._camouflages = camouflages
        self._vehicular = vehicular
        self._data_links = data_links
        self._enable_track_animation = bool(enable_track_animation)
        self._prewarm_wreck_resources = bool(prewarm_wreck_resources)
        self._outfit_factory = outfit_factory
        self._vehicle_stickers_factory = vehicle_stickers_factory
        self.track_animation_error = None
        self._track_animation_reported = False
        self._space_id = int(space_id)
        self._vehicles = {}
        self._next_id = 1000
        self._original_entity = None
        self._original_entities = None
        self._entity_wrapper = None
        self._entities_wrapper = None
        self._hit_testers = {}
        self._descriptors = {}
        self._wreck_descriptor_paths = {}
        self._wreck_requested_paths = set()
        self._wreck_pending_paths = set()
        self._wreck_ready_paths = set()
        self._wreck_failed_paths = set()
        self._wreck_resource_refs = []
        self._wreck_requested_entities = set()
        self._wreck_waiting_entities = set()
        self._wreck_loading_entities = set()
        self._wreck_loading_started = {}
        self._shot_presenter = _RemoteShotPresenter(
            bigworld, math_module, model_assembler, self._space_id)
        self.install()

    def install(self):
        if self._original_entity is not None:
            return
        self._original_entity = self._bigworld.entity
        self._original_entities = self._bigworld.entities
        factory = self

        def entity(entity_id):
            original = factory._original_entity(entity_id)
            if original is not None:
                return original
            vehicle = factory._vehicles.get(entity_id)
            if (_native_visible(vehicle) or
                    _postmortem_visible(vehicle)):
                return vehicle
            return None

        self._entity_wrapper = entity
        self._entities_wrapper = _EntitiesView(
            self._original_entities, self._vehicles)
        self._bigworld.entity = entity
        self._bigworld.entities = self._entities_wrapper

    def _allocate_id(self):
        while True:
            entity_id = self._next_id
            self._next_id += 1
            if (entity_id not in self._vehicles and
                    self._original_entity(entity_id) is None):
                return entity_id

    def prepare_descriptor(self, descriptor):
        """Own the BSP testers before any #1513 bbox consumer runs."""
        get_hit_testers = getattr(descriptor, 'getHitTesters', None)
        if not callable(get_hit_testers):
            raise RuntimeError(
                '#1513 vehicle descriptor hit testers are unavailable')
        for tester in get_hit_testers():
            if tester is None:
                raise RuntimeError('#1513 vehicle hit tester is unavailable')
            key = id(tester)
            owned = self._hit_testers.get(key)
            if owned is tester:
                continue
            if owned is not None:
                raise RuntimeError('#1513 vehicle hit tester identity collided')
            load = getattr(tester, 'loadBspModel', None)
            release = getattr(tester, 'releaseBspModel', None)
            if not callable(load) or not callable(release):
                raise RuntimeError(
                    '#1513 vehicle hit tester lifecycle is unavailable')
            try:
                load()
            except Exception as error:
                try:
                    release()
                except Exception as cleanup_error:
                    raise RuntimeError(
                        '#1513 vehicle hit tester BSP load failed: %s; '
                        'cleanup failed: %s' % (error, cleanup_error))
                raise RuntimeError(
                    '#1513 vehicle hit tester BSP load failed: %s' % error)
            if getattr(tester, 'bbox', None) is None:
                try:
                    release()
                finally:
                    raise RuntimeError(
                        '#1513 vehicle hit tester bbox did not load')
            self._hit_testers[key] = tester
        key = id(descriptor)
        owned = self._descriptors.get(key)
        if owned is not None and owned is not descriptor:
            raise RuntimeError('#1513 vehicle descriptor identity collided')
        self._descriptors[key] = descriptor
        if self._prewarm_wreck_resources:
            self.prewarm_wreck_descriptor(descriptor)
        return descriptor

    def prewarm_wreck_descriptor(self, descriptor):
        """Keep this descriptor's destroyed part resources hot for battle."""
        key = id(descriptor)
        paths = self._wreck_descriptor_paths.get(key)
        if paths is not None:
            return bool(paths)
        getter = getattr(
            self._model_assembler, 'getPartModelsFromDesc', None)
        if not callable(getter):
            self._wreck_descriptor_paths[key] = ()
            return False
        try:
            unique_paths = []
            seen_paths = set()
            for path in getter(descriptor, 'destroyed'):
                if not path or path in seen_paths:
                    continue
                seen_paths.add(path)
                unique_paths.append(path)
            paths = tuple(unique_paths)
        except Exception as error:
            self._wreck_descriptor_paths[key] = ()
            sys.stdout.write(
                '[Offline LAN 0.9.22] wreck resource prewarm unavailable '
                'for %s: %s\n' % (getattr(descriptor, 'name', key), error))
            return False
        self._wreck_descriptor_paths[key] = paths
        pending = tuple(path for path in paths
                        if path not in self._wreck_requested_paths)
        if not pending:
            return bool(paths)
        self._wreck_requested_paths.update(pending)
        self._wreck_pending_paths.update(pending)
        started = time.time()
        descriptor_name = getattr(descriptor, 'name', key)
        sys.stdout.write(
            '[Offline LAN 0.9.22] WRECK prewarm submit vehicle=%s '
            'paths=%d pending=%d\n' % (
                descriptor_name, len(pending),
                len(self._wreck_pending_paths)))
        call_started = time.time()
        try:
            self._bigworld.loadResourceListBG(
                pending, lambda resources:
                self._wreck_resources_loaded(
                    pending, resources, descriptor_name, started))
        except Exception as error:
            self._wreck_pending_paths.difference_update(pending)
            self._wreck_failed_paths.update(pending)
            sys.stdout.write(
                '[Offline LAN 0.9.22] wreck resource prewarm failed '
                'for %s: %s\n' % (getattr(descriptor, 'name', key), error))
            return False
        sys.stdout.write(
            '[Offline LAN 0.9.22] WRECK prewarm return vehicle=%s '
            'call_ms=%.3f\n' % (
                descriptor_name,
                max(0.0, time.time() - call_started) * 1000.0))
        return True

    def prewarm_wrecks_enabled(self):
        return self._prewarm_wreck_resources

    def wreck_prewarm_pending_count(self):
        """Return how many unique destroyed resources still block loading."""
        return len(self._wreck_pending_paths)

    def abandon_pending_wreck_prewarm(self):
        """Stop waiting for resources which missed the startup deadline."""
        pending = tuple(self._wreck_pending_paths)
        if not pending:
            return False
        self._wreck_pending_paths.clear()
        self._wreck_failed_paths.update(pending)
        sys.stdout.write(
            '[Offline LAN 0.9.22] WRECK prewarm abandoned paths=%d\n' %
            len(pending))
        self._resume_waiting_wrecks()
        return True

    def _wreck_resources_loaded(self, paths, resources,
                                descriptor_name=None, started=None):
        if self._wreck_resource_refs is None:
            return
        # A startup timeout deliberately closes this batch before combat.
        # Ignore its late callback instead of turning it into a mid-battle
        # resource residency change and a delayed wreck-model request.
        active_paths = tuple(
            path for path in paths if path in self._wreck_pending_paths)
        if not active_paths:
            return
        failed = set(getattr(resources, 'failedIDs', ()) or ())
        self._wreck_pending_paths.difference_update(active_paths)
        self._wreck_failed_paths.update(
            path for path in active_paths if path in failed)
        self._wreck_ready_paths.update(
            path for path in active_paths if path not in failed)
        # ResourceRefs owns the native resources.  Retain the exact callback
        # object until battle teardown instead of relying on an engine cache
        # eviction policy that is not exposed to Python.
        self._wreck_resource_refs.append(resources)
        elapsed_ms = (0.0 if started is None else
                      max(0.0, time.time() - started) * 1000.0)
        sys.stdout.write(
            '[Offline LAN 0.9.22] WRECK prewarm callback vehicle=%s '
            'paths=%d failed=%d elapsed_ms=%.3f pending=%d\n' % (
                descriptor_name or '-', len(active_paths),
                len(tuple(path for path in active_paths if path in failed)),
                elapsed_ms, len(self._wreck_pending_paths)))
        self._resume_waiting_wrecks()

    def _wreck_resources_ready(self, descriptor):
        paths = self._wreck_descriptor_paths.get(id(descriptor), ())
        return bool(paths) and all(
            path in self._wreck_ready_paths for path in paths)

    def _wreck_resources_failed(self, descriptor):
        paths = self._wreck_descriptor_paths.get(id(descriptor), ())
        return not paths or any(
            path in self._wreck_failed_paths for path in paths)

    def _resume_waiting_wrecks(self):
        for entity_id in tuple(self._wreck_waiting_entities):
            vehicle = self._vehicles.get(entity_id)
            if (vehicle is None or vehicle.model is None or
                    vehicle.typeDescriptor is None):
                self._wreck_waiting_entities.discard(entity_id)
                continue
            descriptor = vehicle.typeDescriptor
            if self._wreck_resources_ready(descriptor):
                self._wreck_waiting_entities.discard(entity_id)
                self._start_hot_wreck_load(entity_id, descriptor)
            elif self._wreck_resources_failed(descriptor):
                self._wreck_waiting_entities.discard(entity_id)

    def _start_hot_wreck_load(self, entity_id, descriptor):
        vehicle = self._vehicles.get(entity_id)
        if (vehicle is None or vehicle.model is None or
                entity_id in self._wreck_loading_entities):
            return False
        self._wreck_loading_entities.add(entity_id)
        self._wreck_loading_started[entity_id] = time.time()
        sys.stdout.write(
            '[Offline LAN 0.9.22] WRECK hot submit id=%s vehicle=%s\n' % (
                entity_id, getattr(descriptor, 'name', '-')))
        prepare_started = time.time()
        try:
            assembler = self._model_assembler.prepareCompoundAssembler(
                descriptor, 'destroyed', self._space_id, False)
            prepared = time.time()
            self._bigworld.loadResourceListBG(
                (assembler,), lambda resources:
                self._wreck_loaded(entity_id, descriptor, resources))
        except Exception as error:
            self._wreck_loading_entities.discard(entity_id)
            self._wreck_loading_started.pop(entity_id, None)
            sys.stdout.write(
                '[Offline LAN 0.9.22] hot wreck assembly failed for %s: '
                '%s\n' % (getattr(descriptor, 'name', entity_id), error))
            return False
        returned = time.time()
        sys.stdout.write(
            '[Offline LAN 0.9.22] WRECK hot return id=%s prepare_ms=%.3f '
            'load_call_ms=%.3f\n' % (
                entity_id,
                max(0.0, prepared - prepare_started) * 1000.0,
                max(0.0, returned - prepared) * 1000.0))
        return True

    def create(self, descriptor, properties, position, rotation):
        entity_id = self._allocate_id()
        vehicle = Vehicle(
            entity_id, descriptor, properties, position, rotation, self._math,
            self._shot_presenter)
        self._vehicles[entity_id] = vehicle
        try:
            self.prepare_descriptor(descriptor)
            assembler = self._model_assembler.prepareCompoundAssembler(
                descriptor, 'undamaged', self._space_id, False)
            self._bigworld.loadResourceListBG(
                (assembler,), lambda resources:
                self._loaded(entity_id, descriptor, resources))
        except Exception as error:
            vehicle.load_error = error
        return entity_id

    def _assemble_track_animation(self, vehicle, descriptor, model):
        """Apply retail fashions, then optionally build native belt animation.

        A client-only vehicle gets no engine-owned filter, so the belts stay
        still whatever is fed to them.  Camouflage and paint still need the
        same fashion setup when that optional controller is disabled.
        """
        camouflages = self._camouflages
        if camouflages is None:
            self._report_track_animation('disabled', None)
            return False
        step = 'prepareFashions'
        scroll = None
        try:
            fashions = camouflages.prepareFashions(False)
            step = 'setupVehicleFashion'
            self._model_assembler.setupVehicleFashion(
                fashions[0], descriptor, False)
            outfit_cd = vehicle.publicInfo.get('outfit', '')
            if outfit_cd:
                outfit, valid = self._vehicle_outfit(vehicle)
            else:
                outfit, valid = None, False
            if valid and outfit_cd:
                step = 'updateFashions'
                camouflages.updateFashions(
                    fashions, descriptor, False, outfit)
            step = 'setupFashions'
            model.setupFashions(fashions)
            if not self._enable_track_animation:
                self._report_track_animation('disabled', None)
                return False
            step = 'createVehicleFilter'
            vehicle_filter = self._model_assembler.createVehicleFilter(
                descriptor)
            step = 'PyTrackScroll'
            scroll = self._bigworld.PyTrackScroll()
            step = 'setFlyingInfo'
            flying = self._attach_flying_info(scroll)
            step = 'activate'
            scroll.activate()
            step = 'setData'
            scroll.setData(vehicle_filter)
            step = 'movementInfo'
            fashions[0].movementInfo = vehicle_filter.movementInfo
        except Exception as error:
            if scroll is not None:
                # ``activate`` registers a native 20 Hz callback and
                # ``setData`` keeps a raw filter pointer.  Unwind both sides
                # when any later initialization step fails.
                try:
                    scroll.deactivate()
                except Exception:
                    pass
                try:
                    scroll.setData(None)
                except Exception:
                    pass
            if self.track_animation_error is None:
                self.track_animation_error = '%s: %s' % (step, error)
            self._report_track_animation(step, error)
            return False
        self._report_track_animation('assembled', None)
        return vehicle.attach_track_animation(
            vehicle_filter, scroll, fashions, flying)

    def _vehicle_outfit(self, vehicle):
        if vehicle._offline_outfit_valid is not None:
            return (vehicle._offline_outfit,
                    vehicle._offline_outfit_valid)
        factory = self._outfit_factory
        if factory is None:
            from gui.shared.gui_items.customization.outfit import Outfit
            factory = Outfit
        compact = vehicle.publicInfo.get('outfit', '')
        try:
            outfit = factory(compact) if compact else factory()
            valid = True
        except Exception as error:
            sys.stdout.write(
                '[Offline LAN 0.9.22] remote vehicle %s outfit was rejected: '
                '%s\n' % (vehicle.id, error))
            outfit = factory()
            valid = False
        vehicle._offline_outfit = outfit
        vehicle._offline_outfit_valid = valid
        return outfit, valid

    def _attach_vehicle_stickers(self, vehicle, descriptor, model):
        factory = self._vehicle_stickers_factory
        stickers = None
        try:
            if factory is None:
                from VehicleStickers import VehicleStickers
                factory = VehicleStickers
            outfit, unused_valid = self._vehicle_outfit(vehicle)
            stickers = factory(
                descriptor,
                int(vehicle.publicInfo.get('marksOnGun', 0) or 0),
                outfit)
            stickers.setClanID(int(
                vehicle.publicInfo.get('clanDBID', 0) or 0))
            stickers.attach(model, False, False)
            vehicle.attach_stickers(stickers)
            return True
        except Exception as error:
            if stickers is not None:
                try:
                    stickers.detach()
                except Exception:
                    pass
            sys.stdout.write(
                '[Offline LAN 0.9.22] remote vehicle %s stickers were '
                'unavailable: %s\n' % (vehicle.id, error))
            return False

    def _attach_flying_info(self, scroll):
        """Give PyTrackScroll the two side-flying links retail always sets."""
        vehicular = self._vehicular
        data_links = self._data_links
        if vehicular is None or data_links is None:
            return None
        provider = vehicular.FlyingInfoProvider()
        scroll.setFlyingInfo(
            data_links.createBoolLink(provider, 'isLeftSideFlying'),
            data_links.createBoolLink(provider, 'isRightSideFlying'))
        return provider

    def _report_track_animation(self, step, error):
        """Say once per battle where the belt assembly stopped."""
        if self._track_animation_reported:
            return False
        self._track_animation_reported = True
        sys.stdout.write(
            '[Offline LAN 0.9.22] bot track assembly %s%s\n' % (
                step, '' if error is None else ' failed: %s' % error))
        return True

    @staticmethod
    def _resource(resources, name):
        if name in getattr(resources, 'failedIDs', ()):
            return None
        try:
            return resources[name]
        except Exception:
            return None

    def _loaded(self, entity_id, descriptor, resources):
        vehicle = self._vehicles.get(entity_id)
        if vehicle is None:
            return
        visual_id = None
        visual = None
        try:
            model = self._resource(resources, descriptor.name)
            if model is None:
                model = self._resource(resources, 'chassis')
            if model is None:
                raise RuntimeError('compound model resource is missing')
            visual_id = self._bigworld.createEntity(
                'OfflineEntity', self._space_id, 0, vehicle.position,
                (vehicle.roll, vehicle.pitch, vehicle.yaw), {})
            try:
                visual = self._original_entities[visual_id]
            except Exception:
                visual = self._original_entity(visual_id)
            if visual is None:
                raise RuntimeError('OfflineEntity did not enter the space')
            self._assemble_track_animation(vehicle, descriptor, model)
            visual.model = model
            vehicle.attach_visual(visual, visual_id, model)
            self._attach_vehicle_stickers(vehicle, descriptor, model)
        except Exception as error:
            # createEntity succeeded before the Python presentation took
            # ownership. Roll that operation back transactionally or the
            # callback leaves an untracked OfflineEntity in the battle space.
            try:
                if vehicle.bw_entity_id == visual_id:
                    vehicle.detach_visual()
                elif visual is not None:
                    visual.model = None
            except Exception:
                vehicle.bw_entity = None
                vehicle.bw_entity_id = None
                vehicle.model = None
                vehicle.isStarted = False
                vehicle.inWorld = False
            if visual_id is not None:
                try:
                    self._bigworld.destroyEntity(visual_id)
                except Exception:
                    pass
            vehicle.load_error = error

    def request_wreck(self, entity_id):
        """Swap to a wreck only after all destroyed parts were prewarmed."""
        vehicle = self._vehicles.get(entity_id)
        if (vehicle is None or vehicle.model is None or
                vehicle.typeDescriptor is None or
                entity_id in self._wreck_requested_entities or
                vehicle.appearance.damageState.isCurrentModelDamaged):
            return False
        descriptor = vehicle.typeDescriptor
        self._wreck_requested_entities.add(entity_id)
        vehicle.retain_wreck_model()
        if self._wreck_resources_ready(descriptor):
            self._start_hot_wreck_load(entity_id, descriptor)
        elif not self._wreck_resources_failed(descriptor):
            # The prewarm began before the countdown. If a very early death
            # wins the race, keep the full compound until its own exact paths
            # are resident, then perform the hot destroyed-model swap.
            self._wreck_waiting_entities.add(entity_id)
        # Failed or unavailable paths deliberately stay on the full compound;
        # never retry them through a cold mid-battle resource request.
        return True

    def _wreck_loaded(self, entity_id, descriptor, resources):
        self._wreck_loading_entities.discard(entity_id)
        started = self._wreck_loading_started.pop(entity_id, None)
        callback_started = time.time()
        vehicle = self._vehicles.get(entity_id)
        if (vehicle is None or vehicle.bw_entity is None or
                vehicle.model is None):
            sys.stdout.write(
                '[Offline LAN 0.9.22] WRECK hot callback id=%s dropped=1 '
                'elapsed_ms=%.3f\n' % (
                    entity_id, 0.0 if started is None else
                    max(0.0, callback_started - started) * 1000.0))
            return
        model = self._resource(resources, descriptor.name)
        if model is None:
            model = self._resource(resources, 'chassis')
        if model is None:
            sys.stdout.write(
                '[Offline LAN 0.9.22] WRECK hot callback id=%s missing=1 '
                'elapsed_ms=%.3f\n' % (
                    entity_id, 0.0 if started is None else
                    max(0.0, callback_started - started) * 1000.0))
            return
        try:
            vehicle.attach_wreck_model(model)
        except Exception as error:
            vehicle.load_error = error
            return
        finished = time.time()
        sys.stdout.write(
            '[Offline LAN 0.9.22] WRECK hot callback id=%s missing=0 '
            'elapsed_ms=%.3f swap_ms=%.3f\n' % (
                entity_id, 0.0 if started is None else
                max(0.0, callback_started - started) * 1000.0,
                max(0.0, finished - callback_started) * 1000.0))

    def get(self, entity_id):
        return self._vehicles.get(entity_id)

    def is_ready(self, entity_id):
        vehicle = self._vehicles.get(entity_id)
        return bool(vehicle is not None and vehicle.isStarted and
                    vehicle.inWorld and vehicle.model is not None)

    def error(self, entity_id):
        vehicle = self._vehicles.get(entity_id)
        return getattr(vehicle, 'load_error', None)

    def play_projectile_tracer(self, descriptor, shell_index, origin,
                               velocity, gravity, max_distance, attacker_id,
                               projectile_id=None, reference_position=None,
                               reference_velocity=None,
                               is_ricochet=False):
        """Play one authoritative launch without consulting a vehicle pose."""
        return self._shot_presenter.play_canonical(
            descriptor, shell_index, origin, velocity, gravity,
            max_distance, attacker_id, projectile_id,
            reference_position, reference_velocity, is_ricochet)

    def admit_projectile_visual(self, attacker_id, projectile_id, now=None):
        """Reserve one bounded cosmetic slot for a canonical projectile."""
        return self._shot_presenter.admit_visual(
            attacker_id, projectile_id, now)

    def stop_projectile_tracer(self, projectile_id, end_position,
                               explosion=None):
        """Retire one canonical tracer after a server terminal event."""
        return self._shot_presenter.stop_canonical(
            projectile_id, end_position, explosion)

    def engine_owns(self, entity_id):
        """Whether BigWorld still knows this client-only entity.

        #1513 ``PyEntities`` carries no ``sq_contains`` and no ``tp_iter``, so
        ``id in BigWorld.entities`` raises ``TypeError``.  Ask it the way it
        can answer.
        """
        if entity_id is None:
            return False
        entities = self._original_entities
        if entities is None:
            entities = getattr(self._bigworld, 'entities', None)
        if entities is None:
            return False
        lookup = getattr(entities, 'get', None)
        if not callable(lookup):
            raise RuntimeError('#1513 entity table lookup is unavailable')
        return lookup(int(entity_id)) is not None

    def engine_active(self):
        """Whether the engine still holds any presentation we created."""
        return any(
            vehicle.bw_entity_id is not None and
            self.engine_owns(vehicle.bw_entity_id)
            for vehicle in self._vehicles.values())

    def destroy(self, entity_id):
        vehicle = self._vehicles.get(entity_id)
        if vehicle is None:
            return False
        visual_id = vehicle.bw_entity_id
        if visual_id is not None and not self.engine_owns(visual_id):
            vehicle.abandon_visual()
        else:
            try:
                vehicle.detach_visual()
            except Exception as error:
                # Do not destroy the native entity while one of its attached
                # subresources still needs an exact retry.
                vehicle.bw_entity_id = visual_id
                raise
            if visual_id is not None:
                try:
                    self._bigworld.destroyEntity(visual_id)
                except Exception as error:
                    # ``detach_visual`` intentionally severs the compound
                    # before destroyEntity.  Retain the stable native id on
                    # failure so a later destroy() can finish that exact
                    # teardown.
                    vehicle.bw_entity_id = visual_id
                    raise
        self._vehicles.pop(entity_id, None)
        self._wreck_requested_entities.discard(entity_id)
        self._wreck_waiting_entities.discard(entity_id)
        self._wreck_loading_entities.discard(entity_id)
        self._wreck_loading_started.pop(entity_id, None)
        return True

    def destroy_all(self):
        first_error = None
        for entity_id in tuple(self._vehicles):
            try:
                self.destroy(entity_id)
            except Exception as error:
                if first_error is None:
                    first_error = error
        # A failed entity remains in ``_vehicles`` with its native id.  Shared
        # BSPs, tracer resources and entity-table wrappers must stay alive
        # until a retry has retired every such owner.
        if first_error is not None:
            raise first_error
        for key, descriptor in tuple(self._descriptors.items()):
            try:
                tank_collision.forget_chassis_shape(descriptor)
            except Exception as error:
                if first_error is None:
                    first_error = error
            else:
                self._descriptors.pop(key, None)
        for key, tester in tuple(self._hit_testers.items()):
            try:
                tester.releaseBspModel()
            except Exception as error:
                if first_error is None:
                    first_error = error
            else:
                self._hit_testers.pop(key, None)
        self._wreck_descriptor_paths = {}
        self._wreck_requested_paths = set()
        self._wreck_pending_paths = set()
        self._wreck_ready_paths = set()
        self._wreck_failed_paths = set()
        self._wreck_resource_refs = None
        self._wreck_requested_entities = set()
        self._wreck_waiting_entities = set()
        self._wreck_loading_entities = set()
        self._wreck_loading_started = {}
        try:
            self._shot_presenter.destroy()
        except Exception as error:
            if first_error is None:
                first_error = error
        try:
            self.restore()
        except Exception as error:
            if first_error is None:
                first_error = error
        if first_error is not None:
            raise first_error

    def restore(self):
        if self._original_entity is None:
            return
        if self._bigworld.entity is self._entity_wrapper:
            self._bigworld.entity = self._original_entity
        if self._bigworld.entities is self._entities_wrapper:
            self._bigworld.entities = self._original_entities
        self._original_entity = None
        self._original_entities = None
        self._entity_wrapper = None
        self._entities_wrapper = None
