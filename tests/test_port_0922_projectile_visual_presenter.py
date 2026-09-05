import io
import math
from pathlib import Path
import sys
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SCRIPTS = ROOT / 'src' / 'res' / 'scripts' / 'client'
sys.path.insert(0, str(CLIENT_SCRIPTS))

from gui.mods.offline_lan_0922.entities.remote_vehicle import (
    RemoteVehicleFactory, _RemoteShotPresenter)
from gui.mods.offline_lan_0922.entities.native_remote_vehicle import (
    NativeRemoteVehicleFactory)


class _Vector(object):

    def __init__(self, x=0.0, y=0.0, z=0.0):
        if not isinstance(x, (int, float)):
            try:
                x, y, z = x[0], x[1], x[2]
            except (TypeError, IndexError):
                x, y, z = x.x, x.y, x.z
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    def scale(self, value):
        return _Vector(self.x * value, self.y * value, self.z * value)

    @property
    def length(self):
        return math.sqrt(self.x * self.x + self.y * self.y +
                         self.z * self.z)

    def normalise(self):
        length = self.length
        if length:
            self.x /= length
            self.y /= length
            self.z /= length


def _xyz(value):
    return (value.x, value.y, value.z)


class _Matrix(object):

    events = None

    def __init__(self, value=None):
        self.rotation = None
        self._translation = _Vector(
            getattr(value, 'translation', _Vector()))

    def setRotateYPR(self, value):
        self.rotation = tuple(float(item) for item in value)

    @property
    def translation(self):
        return self._translation

    @translation.setter
    def translation(self, value):
        self._translation = _Vector(value)
        if self.events is not None:
            self.events.append(('pose', _xyz(self._translation)))


class _Servo(object):

    def __init__(self, provider):
        self.provider = provider


class _Model(object):

    def __init__(self, name, events):
        self.name = name
        self.events = events
        self.motors = []
        self.visible = True
        self.visibleAttachments = False

    def addMotor(self, motor):
        self.events.append(('addMotor', self.name))
        self.motors.append(motor)

    def delMotor(self, motor):
        self.events.append(('delMotor', self.name))
        self.motors.remove(motor)


class _Player(object):

    def __init__(self, events):
        self.playerVehicleID = 42
        self.events = events
        self.models = []
        self.inputHandler = types.SimpleNamespace(
            onProjectileHit=self._on_projectile_hit)
        self.projectile_hits = []
        self.del_model_error = None

    def _on_projectile_hit(self, position, caliber, own_shot):
        self.events.append(('hit', _xyz(position), caliber, own_shot))
        self.projectile_hits.append((position, caliber, own_shot))

    def addModel(self, model):
        self.events.append(('addModel', model.name))
        self.models.append(model)

    def delModel(self, model):
        self.events.append(('delModel', model.name))
        if self.del_model_error is not None:
            raise self.del_model_error
        self.models.remove(model)


class _BigWorld(object):

    def __init__(self, events):
        self.events = events
        self._player = _Player(events)
        self.entity = lambda unused_entity_id: None
        self.entities = {}
        self.camera = lambda: types.SimpleNamespace(
            position=_Vector(91.0, 17.0, -4.0))
        self.Model = lambda name: _Model(name, events)
        self.Servo = _Servo
        self.callbacks = []

    def player(self):
        return self._player

    def callback(self, delay, callback):
        self.events.append(('callback', float(delay)))
        self.callbacks.append((float(delay), callback))
        return len(self.callbacks)

    def run_callbacks(self):
        callbacks = self.callbacks
        self.callbacks = []
        for unused_delay, callback in callbacks:
            callback()


class _ProjectileEffects(object):

    def __init__(self, events):
        self.events = events
        self.attach_calls = []
        self.detach_calls = []
        self.detach_all_calls = []
        self.attach_error = None
        self.detach_error = None

    def attachTo(self, model, data, state, **kwargs):
        self.events.append(('attach', model.name, state))
        self.attach_calls.append((model, data, state, kwargs))
        if self.attach_error is not None:
            raise self.attach_error
        data['attached'] = True

    def detachFrom(self, data, state):
        self.events.append(('detach', state))
        self.detach_calls.append((data, state))
        if self.detach_error is not None:
            raise self.detach_error

    def detachAllFrom(self, data, keep_posteffects=False):
        self.events.append(('detachAll',))
        self.detach_all_calls.append((data, keep_posteffects))
        data.clear()


class _FlockManager(object):

    def __init__(self, events):
        self.events = events
        self.positions = []

    def onProjectile(self, position):
        self.events.append(('flock', _xyz(position)))
        self.positions.append(position)


class _TriggerManager(object):

    def __init__(self, events):
        self.events = events
        self.triggers = []

    def fireTrigger(self, trigger_type):
        self.events.append(('trigger', trigger_type))
        self.triggers.append(trigger_type)


class _NativeMotor(object):

    def __init__(self, origin, velocity, start, gravity):
        self.origin = _Vector(origin)
        self.velocity = _Vector(velocity)
        self.start = _Vector(start)
        self.gravity = gravity
        self.elapsed = 0.0

    def advance(self, dt):
        self.elapsed += dt
        t = self.elapsed
        return _Vector(self.start.x + self.velocity.x * t,
                       self.start.y + self.velocity.y * t -
                       0.5 * self.gravity * t * t,
                       self.start.z + self.velocity.z * t)


class _ProjectileMover(object):
    """Exercise the reviewed stock guards, rows and ownership callbacks.

    Motor stepping is a fake engine callback, not evidence of native physics
    or Windows rendering. Its purpose is to expose competing Python writers.
    """

    instances = []
    explode_error = None
    destroy_error = None
    add_error = None
    hide_error = None
    reject_add = False
    on_add = None
    bigworld = None

    def __init__(self):
        self.calls = []
        self.explode_calls = []
        self.hide_calls = []
        self.hold_calls = []
        self.destroy_calls = 0
        self.space_ids = []
        self._ProjectileMover__projectiles = {}
        self.__class__.instances.append(self)

    def add(self, *args):
        if not self.space_ids:
            return
        self.calls.append(args)
        if self.__class__.on_add is not None:
            self.__class__.on_add()
        if self.__class__.add_error is not None:
            raise self.__class__.add_error
        if self.__class__.reject_add:
            return
        (shot_id, effects, gravity, origin, velocity, start,
         maximum, attacker_id, camera_position) = args
        if effects.get('artilleryID') is not None:
            return
        if sum((a - b) ** 2 for a, b in zip(_xyz(start), _xyz(origin))) > 400:
            start = origin
        own = attacker_id == self.bigworld.player().playerVehicleID
        motor = _NativeMotor(origin, velocity, start, gravity)
        model = self.bigworld.Model(effects['projectile'][int(own)])
        model.position = _Vector(start)
        row = {'model': model, 'motor': motor, 'effectsDescr': effects,
               'showExplosion': False, 'fireMissedTrigger': own,
               'autoScaleProjectile': own, 'attackerID': attacker_id,
               'effectsData': {}}
        self.bigworld.player().addModel(model)
        model.addMotor(motor)
        model.visible = False
        model.visibleAttachments = True
        effects['projectile'][2].attachTo(
            model, row['effectsData'], 'flying',
            isPlayerVehicle=own, isArtillery=False)
        self._ProjectileMover__projectiles[shot_id] = row

    def engine_frame(self, dt):
        for shot_id, row in self._ProjectileMover__projectiles.items():
            if shot_id > 0:
                row['model'].position = row['motor'].advance(dt)

    def _ProjectileMover__notifyProjectileHit(self, position, row):
        self.bigworld.player().inputHandler.onProjectileHit(
            position, row['effectsDescr']['caliber'],
            row['autoScaleProjectile'])

    def explode(self, *args):
        self.explode_calls.append(args)
        if self.__class__.explode_error is not None:
            raise self.__class__.explode_error
        row = self._ProjectileMover__projectiles.get(args[0])
        if row is not None:
            row['fireMissedTrigger'] = False
            row['showExplosion'] = True
            self._ProjectileMover__notifyProjectileHit(args[3], row)

    def hide(self, shot_id, position):
        if self.__class__.hide_error is not None:
            raise self.__class__.hide_error
        self.hide_calls.append((shot_id, position))
        row = self._ProjectileMover__projectiles.pop(shot_id, None)
        if row is not None:
            self._ProjectileMover__projectiles[-shot_id] = row
            row['fireMissedTrigger'] = False
            row['showExplosion'] = False
            self._ProjectileMover__notifyProjectileHit(position, row)

    def hold(self, shot_id):
        self.hold_calls.append(shot_id)

    def expire(self, shot_id):
        row = self._ProjectileMover__projectiles.pop(shot_id, None)
        if row is None:
            return
        row['effectsDescr']['projectile'][2].detachAllFrom(row['effectsData'])
        row['model'].delMotor(row['motor'])
        self.bigworld.player().delModel(row['model'])
        if row['fireMissedTrigger']:
            import TriggersManager
            TriggersManager.g_manager.fireTrigger(
                TriggersManager.TRIGGER_TYPE.PLAYER_SHOT_MISSED)

    def setSpaceID(self, space_id):
        self.space_ids.append(space_id)

    def destroy(self):
        self.destroy_calls += 1
        if self.__class__.destroy_error is not None:
            raise self.__class__.destroy_error
        for shot_id in list(self._ProjectileMover__projectiles):
            # Stock destroy calls __delProjectile, not its expiry callback.
            self._ProjectileMover__projectiles[shot_id]['fireMissedTrigger'] = False
            self.expire(shot_id)


def _descriptor(effects_index=7):
    shell = types.SimpleNamespace(effectsIndex=effects_index)
    shot = types.SimpleNamespace(
        shell=shell, speed=925.0, gravity=9.81, maxDistance=640.0)
    return types.SimpleNamespace(
        activeGunShotIndex=0,
        gun=types.SimpleNamespace(shots=[shot]))


def _effects(events, artillery=False):
    projectile_effects = _ProjectileEffects(events)
    descriptor = {
        'projectile': (
            'remote-projectile.model', 'own-projectile.model',
            projectile_effects),
        'groundHit': ('stages', 'effect', None),
        'caliber': 0.12,
    }
    if artillery:
        descriptor['artilleryID'] = 91
        descriptor.pop('projectile')
    return descriptor, projectile_effects


def _modules(effects):
    items = types.ModuleType('items')
    items.vehicles = types.SimpleNamespace(
        g_cache=types.SimpleNamespace(shotEffects={7: effects}))
    projectile_mover = types.ModuleType('ProjectileMover')
    projectile_mover.ProjectileMover = _ProjectileMover
    return {'items': items, 'ProjectileMover': projectile_mover}


class ProjectileVisualPresenterTests(unittest.TestCase):

    def setUp(self):
        _ProjectileMover.instances[:] = []
        _ProjectileMover.explode_error = None
        _ProjectileMover.destroy_error = None
        _ProjectileMover.add_error = None
        _ProjectileMover.hide_error = None
        _ProjectileMover.reject_add = False
        _ProjectileMover.on_add = None
        self.events = []
        _Matrix.events = self.events
        self.bigworld = _BigWorld(self.events)
        _ProjectileMover.bigworld = self.bigworld
        self.math = types.SimpleNamespace(Vector3=_Vector, Matrix=_Matrix)
        self.effects, self.projectile_effects = _effects(self.events)
        self.flock = _FlockManager(self.events)
        self.triggers = _TriggerManager(self.events)
        flock_module = types.ModuleType('FlockManager')
        flock_module.getManager = lambda: self.flock
        triggers_module = types.ModuleType('TriggersManager')
        triggers_module.g_manager = self.triggers
        triggers_module.TRIGGER_TYPE = types.SimpleNamespace(
            PLAYER_SHOT_MISSED='player-shot-missed')
        self.flock_patch = mock.patch.dict(
            sys.modules, {
                'FlockManager': flock_module,
                'TriggersManager': triggers_module,
            })
        self.flock_patch.start()

    def tearDown(self):
        self.flock_patch.stop()
        _Matrix.events = None

    def _factory(self):
        return RemoteVehicleFactory(
            self.bigworld, self.math, types.SimpleNamespace(), 7)

    def _launch(self, factory, projectile_id='round:shot', attacker_id=42,
                origin=(1.0, 2.0, 3.0),
                reference_position=(3.0, 4.0, 5.0),
                reference_velocity=(100.0, 10.0, 0.0),
                effects=None, is_ricochet=False, visual_start=None):
        effects = self.effects if effects is None else effects
        with mock.patch.dict(sys.modules, _modules(effects)):
            return factory.play_projectile_tracer(
                _descriptor(), 0, origin,
                (120.0, 20.0, 0.0), 9.81, 640.0, attacker_id,
                projectile_id, reference_position, reference_velocity,
                is_ricochet, visual_start)

    def test_player_and_bot_use_space_bound_stock_native_motors(self):
        factory = self._factory()
        self.bigworld.Servo = mock.Mock(side_effect=AssertionError('second owner'))
        first = self._launch(factory, 'player:1', attacker_id=42)
        second = self._launch(factory, 'bot:2', attacker_id=77)
        mover = factory._shot_presenter._mover
        self.assertEqual([7], mover.space_ids)
        self.assertEqual(2, len(mover.calls))
        self.assertNotEqual(first, second)
        self.bigworld.Servo.assert_not_called()
        for shot_id, own in ((first, True), (second, False)):
            row = mover._ProjectileMover__projectiles[shot_id]
            self.assertIsInstance(row['motor'], _NativeMotor)
            self.assertFalse(row['model'].visible)
            self.assertTrue(row['model'].visibleAttachments)
            self.assertFalse(row['fireMissedTrigger'])
            self.assertTrue(row['_offlineLANCanonical'])
            self.assertEqual(own, row['autoScaleProjectile'])
        self.assertEqual('own-projectile.model', self.bigworld.player().models[0].name)
        self.assertEqual('remote-projectile.model', self.bigworld.player().models[1].name)
        self.assertEqual((3.0, 4.0, 5.0), _xyz(mover.calls[0][3]))
        self.assertEqual((100.0, 10.0, 0.0), _xyz(mover.calls[0][4]))
        self.assertEqual((91.0, 17.0, -4.0), _xyz(mover.calls[0][-1]))

    def test_native_flight_does_not_wait_for_a_worker_cursor(self):
        factory = self._factory()
        shot_id = self._launch(factory)
        mover = factory._shot_presenter._mover
        mover.engine_frame(0.4)
        row = mover._ProjectileMover__projectiles[shot_id]
        position = _xyz(row['model'].position)
        self.assertGreater(position[0], 3.0)
        self.assertEqual(shot_id, self._launch(factory))
        self.assertEqual(1, len(mover.calls))
        self.assertEqual(position, _xyz(row['model'].position))
        self.assertFalse(hasattr(factory, 'update_projectile_visual'))
        self.assertEqual([], self.triggers.triggers)

    def test_current_muzzle_and_stock_distance_guard_do_not_change_reference(self):
        for start, expected in (((8, 4, 5), (8, 4, 5)),
                                ((24, 4, 5), (3, 4, 5))):
            with self.subTest(start=start):
                factory = self._factory()
                shot_id = self._launch(factory, visual_start=start)
                mover = factory._shot_presenter._mover
                row = mover._ProjectileMover__projectiles[shot_id]
                self.assertEqual(expected, _xyz(row['model'].position))
                self.assertEqual((3, 4, 5), _xyz(row['motor'].origin))
                factory.destroy_all()

    def test_worker_vehicle_terminal_hides_at_its_endpoint_once(self):
        factory = self._factory()
        shot_id = self._launch(factory)
        mover = factory._shot_presenter._mover
        mover.engine_frame(0.8)
        self.assertTrue(factory.stop_projectile_tracer('round:shot', (23, 2, 1)))
        self.assertEqual([(shot_id, (23, 2, 1))], [
            (key, _xyz(point)) for key, point in mover.hide_calls])
        self.assertIn(-shot_id, mover._ProjectileMover__projectiles)
        self.assertEqual(1, len(self.bigworld.player().projectile_hits))
        self.assertEqual([], self.triggers.triggers)
        self.assertFalse(factory.stop_projectile_tracer('round:shot', (23, 2, 1)))
        mover.expire(-shot_id)
        self.assertEqual([], self.bigworld.player().models)
        self.assertEqual(1, len(self.bigworld.player().projectile_hits))

    def test_world_terminal_uses_stock_explode_and_authoritative_feedback(self):
        for attacker, missed_count in ((42, 1), (77, 0)):
            with self.subTest(attacker=attacker):
                self.triggers.triggers[:] = []
                factory = self._factory()
                shot_id = self._launch(factory, attacker_id=attacker)
                mover = factory._shot_presenter._mover
                self.assertTrue(factory.stop_projectile_tracer(
                    'round:shot', (20, 0, 0),
                    explosion=(self.effects, 'ground', (100, 0, 0)), missed=True))
                self.assertEqual(shot_id, mover.explode_calls[0][0])
                self.assertEqual((1, 0, 0), _xyz(mover.explode_calls[0][4]))
                self.assertEqual([], mover.hide_calls)
                self.assertEqual(missed_count, len(self.triggers.triggers))
                factory.destroy_all()
                self.assertEqual(missed_count, len(self.triggers.triggers))

    def test_native_expiry_neither_reports_a_miss_nor_relaunches_on_retry(self):
        factory = self._factory()
        shot_id = self._launch(factory)
        mover = factory._shot_presenter._mover
        mover.expire(shot_id)
        self.assertEqual([], self.triggers.triggers)
        self.assertEqual([], self.bigworld.player().projectile_hits)
        self.assertEqual(shot_id, self._launch(factory))
        self.assertEqual(1, len(mover.calls))
        self.assertTrue(factory.stop_projectile_tracer(
            'round:shot', (100, 0, 0),
            explosion=(self.effects, 'ground', (1, 0, 0)), missed=True))
        self.assertEqual(shot_id, mover.explode_calls[0][0])
        self.assertEqual({}, mover._ProjectileMover__projectiles)
        self.assertEqual(1, len(self.triggers.triggers))
        self.assertEqual(1, len(self.bigworld.player().projectile_hits))

    def test_ricochet_gets_a_new_held_motor_and_old_tail_cannot_delete_it(self):
        factory = self._factory()
        first = self._launch(factory)
        mover = factory._shot_presenter._mover
        self.assertTrue(factory.stop_projectile_tracer('round:shot', (20, 0, 0)))
        second = self._launch(factory, is_ricochet=True)
        self.assertNotEqual(first, second)
        self.assertEqual([second], mover.hold_calls)
        mover.expire(-first)
        self.assertIn(second, mover._ProjectileMover__projectiles)
        self.assertEqual(second, factory._shot_presenter._projectile_shots[
            'round:shot']['visual_id'])

    def test_capacity_counts_native_tails_without_detaching_other_motors(self):
        factory = self._factory()
        presenter = factory._shot_presenter
        presenter._MAX_ACTIVE_TOTAL = 1
        first = self._launch(factory, 'first')
        mover = presenter._mover
        self.assertTrue(factory.stop_projectile_tracer('first', (20, 0, 0)))
        self.assertFalse(self._launch(factory, 'second'))
        self.assertIn(-first, mover._ProjectileMover__projectiles)
        mover.expire(-first)
        # Cosmetic denial is final for this shot, so freeing a tail cannot
        # make an old launch suddenly appear seconds after its muzzle effect.
        self.assertFalse(self._launch(factory, 'second'))
        self.assertTrue(self._launch(factory, 'third'))
        self.assertEqual([], self.triggers.triggers)

    def test_non_ledgered_stock_tracer_keeps_its_original_feedback(self):
        factory = self._factory()
        with mock.patch.dict(sys.modules, _modules(self.effects)):
            shot_id = factory._shot_presenter._play_legacy_tracer(
                _descriptor(), 0, (1, 2, 3), (100, 0, 0), 9.81, 640, 42)
        mover = factory._shot_presenter._mover
        self.assertTrue(shot_id)
        self.assertNotIn('_offlineLANCanonical',
                         mover._ProjectileMover__projectiles[shot_id])
        mover.hide(shot_id, _Vector(20, 2, 3))
        self.assertEqual(1, len(self.bigworld.player().projectile_hits))

    def test_reset_retires_native_owners_without_combat_feedback(self):
        factory = self._factory()
        self._launch(factory)
        mover = factory._shot_presenter._mover
        self.assertTrue(factory.reset_projectile_visuals())
        self.assertEqual(1, mover.destroy_calls)
        self.assertEqual([], self.bigworld.player().models)
        self.assertEqual([], self.bigworld.player().projectile_hits)
        self.assertEqual([], self.triggers.triggers)
        self.assertTrue(self._launch(factory, 'next-round'))
        self.assertIsNot(mover, factory._shot_presenter._mover)

    def test_failed_reset_retains_native_owner_and_disables_new_cosmetics(self):
        factory = self._factory()
        self._launch(factory)
        mover = factory._shot_presenter._mover
        _ProjectileMover.destroy_error = RuntimeError('retirement failed')
        with mock.patch('sys.stdout', new=io.StringIO()):
            self.assertFalse(factory.reset_projectile_visuals())
        self.assertIs(mover, factory._shot_presenter._mover)
        self.assertFalse(self._launch(factory, 'next-shot'))
        _ProjectileMover.destroy_error = None
        self.assertTrue(factory.reset_projectile_visuals())
        self.assertIsNone(factory._shot_presenter._mover)
        self.assertEqual([], self.bigworld.player().models)

    def test_native_add_rejection_can_retry_without_duplicate_resources(self):
        factory = self._factory()
        _ProjectileMover.reject_add = True
        with mock.patch('sys.stdout', new=io.StringIO()):
            self.assertFalse(self._launch(factory))
        self.assertEqual({}, factory._shot_presenter._projectile_shots)
        self.assertEqual([], self.bigworld.player().models)
        _ProjectileMover.reject_add = False
        self.assertTrue(self._launch(factory))
        self.assertEqual(1, len(self.bigworld.player().models))

    def test_identity_is_installed_before_native_creation_can_reenter(self):
        factory = self._factory()
        seen = []
        _ProjectileMover.on_add = lambda: seen.append(dict(
            factory._shot_presenter._projectile_shots['round:shot']))
        self.assertTrue(self._launch(factory))
        self.assertEqual(1, len(seen))
        self.assertFalse(seen[0]['started'])
        self.assertEqual(1000000, seen[0]['visual_id'])

    def test_native_add_exception_retains_owner_without_retrying(self):
        factory = self._factory()
        _ProjectileMover.add_error = RuntimeError('partial creation')
        with mock.patch('sys.stdout', new=io.StringIO()):
            self.assertFalse(self._launch(factory))
        mover = factory._shot_presenter._mover
        self.assertFalse(self._launch(factory))
        self.assertEqual(1, len(mover.calls))
        self.assertIn('round:shot', factory._shot_presenter._projectile_shots)
        self.assertTrue(factory.reset_projectile_visuals())

    def test_failed_terminal_cannot_reuse_old_motor_as_a_ricochet(self):
        factory = self._factory()
        first = self._launch(factory)
        mover = factory._shot_presenter._mover
        _ProjectileMover.hide_error = RuntimeError('native hide failed')
        with mock.patch('sys.stdout', new=io.StringIO()):
            self.assertFalse(factory.stop_projectile_tracer(
                'round:shot', (20, 0, 0)))
        self.assertFalse(self._launch(factory, is_ricochet=True))
        self.assertIn(first, mover._ProjectileMover__projectiles)
        self.assertEqual([], mover.hold_calls)
        self.assertTrue(self._launch(factory, 'independent-shot'))
        _ProjectileMover.hide_error = None
        self.assertTrue(factory.stop_projectile_tracer('round:shot', (20, 0, 0)))

    def test_material_effect_failure_still_hides_and_does_not_block_later_flight(self):
        factory = self._factory()
        shot_id = self._launch(factory)
        mover = factory._shot_presenter._mover
        _ProjectileMover.explode_error = RuntimeError('material failure')
        with mock.patch('sys.stdout', new=io.StringIO()):
            self.assertTrue(factory.stop_projectile_tracer(
                'round:shot', (20, 0, 0),
                explosion=(self.effects, 'ground', (1, 0, 0))))
        self.assertIn(-shot_id, mover._ProjectileMover__projectiles)
        self.assertTrue(self._launch(factory, 'next-shot'))
        self.assertEqual(2, len(mover.calls))

    def test_unmapped_or_denied_terminal_does_not_fabricate_visuals(self):
        factory = self._factory()
        self.assertFalse(factory.stop_projectile_tracer(
            'unowned', (20, 0, 0),
            explosion=(self.effects, 'ground', (1, 0, 0))))
        self.assertIsNone(factory._shot_presenter._mover)
        factory._shot_presenter._visual_admissions['denied'] = (42, False)
        self.assertFalse(factory.stop_projectile_tracer(
            'denied', (20, 0, 0),
            explosion=(self.effects, 'ground', (1, 0, 0))))

    def test_admitted_missing_tracer_keeps_late_world_explosion_without_add(self):
        factory = self._factory()
        self.assertTrue(factory.admit_projectile_visual(42, 'admitted', 0.0))
        with mock.patch.dict(sys.modules, _modules(self.effects)):
            self.assertTrue(factory.stop_projectile_tracer(
                'admitted', (20, 0, 0),
                explosion=(self.effects, 'ground', (1, 0, 0))))
        mover = factory._shot_presenter._mover
        self.assertEqual([], mover.calls)
        self.assertEqual(1, len(mover.explode_calls))
        self.assertFalse(factory.stop_projectile_tracer(
            'admitted', (20, 0, 0),
            explosion=(self.effects, 'ground', (1, 0, 0))))

    def test_combat_equipment_artillery_is_not_admitted_to_native_shell_ledger(self):
        factory = self._factory()
        effects, unused = _effects(self.events, artillery=True)
        with mock.patch('sys.stdout', new=io.StringIO()):
            self.assertFalse(self._launch(factory, effects=effects))
        self.assertIsNone(factory._shot_presenter._mover)
        self.assertEqual([], self.bigworld.player().models)

    def test_destroy_is_safe_twice_and_late_native_expiry_is_harmless(self):
        factory = self._factory()
        shot_id = self._launch(factory)
        presenter = factory._shot_presenter
        mover = presenter._mover
        presenter.destroy()
        presenter.destroy()
        mover.expire(shot_id)
        self.assertEqual(1, mover.destroy_calls)
        self.assertEqual([], self.bigworld.player().models)
        self.assertEqual([], self.triggers.triggers)
        self.assertFalse(self._launch(factory, 'after-destroy'))

    def test_both_factories_forward_native_launch_and_terminal(self):
        for cls in (RemoteVehicleFactory, NativeRemoteVehicleFactory):
            with self.subTest(factory=cls.__name__):
                factory = object.__new__(cls)
                factory._shot_presenter = mock.Mock()
                factory.play_projectile_tracer(
                    'descriptor', 2, (1, 2, 3), (100, 0, 0), 9.81,
                    400, 42, 'round:shot', (2, 2, 3), (100, 0, 0),
                    False, (3, 2, 3))
                factory._shot_presenter.play_canonical.assert_called_once_with(
                    'descriptor', 2, (1, 2, 3), (100, 0, 0), 9.81,
                    400, 42, 'round:shot', (2, 2, 3), (100, 0, 0),
                    False, (3, 2, 3))
                self.assertFalse(hasattr(factory, 'update_projectile_visual'))


if __name__ == '__main__':
    unittest.main()
