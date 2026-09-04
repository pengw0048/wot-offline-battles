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


class _ProjectileMover(object):

    instances = []
    explode_error = None
    destroy_error = None

    def __init__(self):
        self.calls = []
        self.explode_calls = []
        self.destroy_calls = 0
        self.space_ids = []
        self._ProjectileMover__projectiles = {}
        self.__class__.instances.append(self)

    def add(self, *args):
        self.calls.append(args)
        if args[1].get('artilleryID') is None:
            self._ProjectileMover__projectiles[args[0]] = object()

    def explode(self, *args):
        self.explode_calls.append(args)
        if self.__class__.explode_error is not None:
            raise self.__class__.explode_error

    def setSpaceID(self, space_id):
        self.space_ids.append(space_id)

    def destroy(self):
        self.destroy_calls += 1
        if self.__class__.destroy_error is not None:
            raise self.__class__.destroy_error


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
        self.events = []
        _Matrix.events = self.events
        self.bigworld = _BigWorld(self.events)
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
                reference_position=(3.0, 4.0, 5.0),
                reference_velocity=(100.0, 10.0, 0.0),
                effects=None, is_ricochet=False):
        effects = self.effects if effects is None else effects
        with mock.patch.dict(sys.modules, _modules(effects)):
            return factory.play_projectile_tracer(
                _descriptor(), 0, (1.0, 2.0, 3.0),
                (120.0, 20.0, 0.0), 9.81, 640.0, attacker_id,
                projectile_id, reference_position, reference_velocity,
                is_ricochet)

    def test_player_and_bot_use_visible_stock_effects_on_controlled_servos(self):
        factory = self._factory()

        player_visual = self._launch(factory, 'player:1', attacker_id=42)
        bot_visual = self._launch(factory, 'bot:1', attacker_id=99)

        self.assertEqual(1000000, player_visual)
        self.assertEqual(1000001, bot_visual)
        self.assertEqual([], _ProjectileMover.instances)
        models = self.bigworld._player.models
        self.assertEqual(
            ['own-projectile.model', 'remote-projectile.model'],
            [model.name for model in models])
        self.assertTrue(all(model.visibleAttachments for model in models))
        self.assertTrue(all(not model.visible for model in models))
        self.assertTrue(all(len(model.motors) == 1 for model in models))
        self.assertTrue(all(
            isinstance(model.motors[0], _Servo) for model in models))
        self.assertEqual((3.0, 4.0, 5.0),
                         _xyz(models[0].motors[0].provider.translation))
        calls = self.projectile_effects.attach_calls
        self.assertEqual(['flying', 'flying'], [call[2] for call in calls])
        self.assertEqual(
            [True, False],
            [call[3]['isPlayerVehicle'] for call in calls])
        self.assertEqual([False, False],
                         [call[3]['isArtillery'] for call in calls])

    def test_non_ledgered_show_shooting_keeps_self_retiring_stock_tracer(self):
        presenter = _RemoteShotPresenter(
            self.bigworld, self.math, types.SimpleNamespace(), 7)
        node = types.SimpleNamespace(translation=_Vector(4.0, 5.0, 6.0))
        vehicle = types.SimpleNamespace(
            id=99,
            model=types.SimpleNamespace(node=mock.Mock(return_value=node)),
            typeDescriptor=_descriptor(), _offlineLANShotIndex=0,
            position=_Vector(), yaw=0.0, _aim_yaw=math.pi / 2.0,
            _gun_pitch=-0.2)

        with mock.patch.dict(sys.modules, _modules(self.effects)):
            self.assertTrue(presenter.play_tracer(vehicle))

        self.assertEqual([], self.bigworld._player.models)
        mover = _ProjectileMover.instances[0]
        self.assertEqual(1, len(mover.calls))
        args = mover.calls[0]
        self.assertIs(self.effects, args[1])
        self.assertEqual((4.0, 5.0, 6.0), _xyz(args[3]))
        self.assertEqual((4.0, 5.0, 6.0), _xyz(args[5]))
        self.assertEqual(99, args[7])

    def test_update_moves_same_pose_and_duplicate_launch_does_not_recreate(self):
        factory = self._factory()
        visual_id = self._launch(factory)
        entry = factory._shot_presenter._projectile_shots['round:shot']
        pose = entry['pose']
        model = entry['model']

        duplicate = self._launch(
            factory, reference_position=(999.0, 999.0, 999.0))
        self.assertTrue(factory.update_projectile_visual(
            'round:shot', (11.0, 12.0, 13.0), (0.0, -4.0, 80.0)))

        self.assertEqual(visual_id, duplicate)
        self.assertIs(pose, model.motors[0].provider)
        self.assertEqual((11.0, 12.0, 13.0), _xyz(pose.translation))
        self.assertEqual(1, len(self.bigworld._player.models))
        self.assertEqual(1, len(self.projectile_effects.attach_calls))
        self.assertEqual([], _ProjectileMover.instances)

    def test_terminal_pins_then_keeps_stock_tail_before_owner_cleanup(self):
        factory = self._factory()
        self._launch(factory)
        model = self.bigworld._player.models[0]
        self.events[:] = []

        self.assertTrue(factory.stop_projectile_tracer(
            'round:shot', (31.0, 7.0, -2.0)))

        self.assertEqual(('pose', (31.0, 7.0, -2.0)), self.events[0])
        self.assertLess(
            self.events.index(('pose', (31.0, 7.0, -2.0))),
            self.events.index(('hit', (31.0, 7.0, -2.0), 0.12, True)))
        self.assertLess(
            self.events.index(('hit', (31.0, 7.0, -2.0), 0.12, True)),
            self.events.index(('detach', 'stopFlying')))
        self.assertEqual(1, len(model.motors))
        self.assertEqual([model], self.bigworld._player.models)
        presenter = factory._shot_presenter
        self.assertNotIn('round:shot', presenter._projectile_shots)
        self.assertEqual(1, len(presenter._projectile_tails))
        self.assertNotIn(
            'round:shot', factory._shot_presenter._visual_admissions)
        self.assertFalse(factory.stop_projectile_tracer(
            'round:shot', (31.0, 7.0, -2.0)))
        self.assertEqual([], _ProjectileMover.instances)
        self.assertEqual(2, len(self.flock.positions))
        self.assertEqual((31.0, 7.0, -2.0),
                         _xyz(self.flock.positions[-1]))
        self.assertEqual(1, len(self.bigworld._player.projectile_hits))
        self.assertEqual(1, len(self.bigworld.callbacks))
        self.assertEqual(2.0, self.bigworld.callbacks[0][0])
        self.assertEqual([], self.projectile_effects.detach_all_calls)

        self.bigworld.run_callbacks()

        self.assertEqual([], model.motors)
        self.assertEqual([], self.bigworld._player.models)
        self.assertNotIn(
            'round:shot', factory._shot_presenter._projectile_shots)
        self.assertEqual({}, factory._shot_presenter._projectile_tails)
        self.assertEqual(1, len(self.projectile_effects.detach_all_calls))

    def test_world_terminal_cleans_tracer_then_uses_unknown_id_explosion(self):
        factory = self._factory()
        self._launch(factory, projectile_id='world:shot')
        self.events[:] = []
        explosion = (self.effects, 'ground', (2.0, -2.0, 0.0))

        with mock.patch.dict(sys.modules, _modules(self.effects)):
            self.assertTrue(factory.stop_projectile_tracer(
                'world:shot', (12.0, 0.0, 3.0), explosion=explosion,
                missed=True))

        self.assertEqual(1, len(self.bigworld._player.models))
        mover = _ProjectileMover.instances[0]
        self.assertEqual([], mover.calls)
        self.assertEqual([7], mover.space_ids)
        self.assertEqual(1, len(mover.explode_calls))
        args = mover.explode_calls[0]
        self.assertEqual(1000001, args[0])
        self.assertNotIn(args[0], mover._ProjectileMover__projectiles)
        self.assertEqual((12.0, 0.0, 3.0), _xyz(args[3]))
        self.assertAlmostEqual(1.0, args[4].length)
        self.assertEqual(['player-shot-missed'], self.triggers.triggers)
        self.assertLess(
            self.events.index(('detach', 'stopFlying')),
            self.events.index(('callback', 2.0)))

        self.bigworld.run_callbacks()
        self.assertEqual([], self.bigworld._player.models)

    def test_bot_world_terminal_does_not_fire_player_missed_trigger(self):
        factory = self._factory()
        self._launch(
            factory, projectile_id='bot:world', attacker_id=99)

        self.assertTrue(factory.stop_projectile_tracer(
            'bot:world', (12.0, 0.0, 3.0), missed=True))

        self.assertEqual([], self.triggers.triggers)

    def test_pending_world_terminal_preserves_explosion_without_late_tracer(self):
        factory = self._factory()
        presenter = factory._shot_presenter
        self.assertTrue(factory.admit_projectile_visual(
            42, 'pending:world', now=10.0))

        with mock.patch.dict(sys.modules, _modules(self.effects)):
            self.assertTrue(factory.stop_projectile_tracer(
                'pending:world', (12.0, 0.0, 3.0),
                explosion=(self.effects, 'ground', (1.0, -1.0, 0.0))))

        self.assertEqual([], self.bigworld._player.models)
        mover = _ProjectileMover.instances[0]
        self.assertEqual([], mover.calls)
        self.assertEqual(1, len(mover.explode_calls))
        self.assertNotIn('pending:world', presenter._visual_admissions)

    def test_unmapped_terminal_cannot_bypass_visual_admission(self):
        factory = self._factory()

        with mock.patch.dict(sys.modules, _modules(self.effects)):
            self.assertFalse(factory.stop_projectile_tracer(
                'unknown', (12.0, 0.0, 3.0),
                explosion=(self.effects, 'ground', (1.0, -1.0, 0.0))))

        self.assertEqual([], _ProjectileMover.instances)
        self.assertEqual([], self.bigworld._player.models)

    def test_pressure_retires_oldest_controlled_visual_without_native_rows(self):
        presenter = _RemoteShotPresenter(
            self.bigworld, self.math, types.SimpleNamespace(), 7)
        with mock.patch.dict(sys.modules, _modules(self.effects)):
            for index in range(25):
                projectile_id = 'rapid:%d' % index
                self.assertTrue(presenter.admit_visual(
                    42, projectile_id, now=float(index)))
                self.assertTrue(presenter.play_canonical(
                    _descriptor(), 0, (float(index), 1.0, 0.0),
                    (100.0, 0.0, 0.0), 9.81, 640.0, 42,
                    projectile_id))

        self.assertEqual(24, len(presenter._projectile_shots))
        self.assertNotIn('rapid:0', presenter._projectile_shots)
        self.assertIn('rapid:24', presenter._projectile_shots)
        self.assertEqual(24, len(self.bigworld._player.models))
        self.assertEqual([], _ProjectileMover.instances)
        self.assertFalse(presenter._visual_admissions['rapid:0'][1])

    def test_failed_terminal_detach_keeps_owner_for_retry(self):
        factory = self._factory()
        self._launch(factory, projectile_id='retry:terminal')
        self.projectile_effects.detach_error = RuntimeError('detach failed')

        self.assertFalse(factory.stop_projectile_tracer(
            'retry:terminal', (10.0, 1.0, 0.0)))
        self.assertIn(
            'retry:terminal', factory._shot_presenter._projectile_shots)
        self.assertEqual(1, len(self.bigworld._player.models))

        self.projectile_effects.detach_error = None
        self.assertTrue(factory.stop_projectile_tracer(
            'retry:terminal', (10.0, 1.0, 0.0)))
        self.assertEqual(1, len(self.bigworld._player.models))
        self.bigworld.run_callbacks()
        self.assertEqual([], self.bigworld._player.models)

    def test_ricochet_reuses_id_without_losing_old_tail_or_new_segment(self):
        factory = self._factory()
        self._launch(factory, projectile_id='reused:1')
        self.assertTrue(factory.stop_projectile_tracer(
            'reused:1', (10.0, 1.0, 0.0)))

        self.assertTrue(self._launch(
            factory, projectile_id='reused:1', is_ricochet=True,
            reference_position=(10.0, 1.0, 0.0),
            reference_velocity=(-80.0, 4.0, 0.0)))
        replacement = factory._shot_presenter._projectile_shots['reused:1']
        self.assertEqual(2, len(self.bigworld._player.models))
        self.assertEqual(1, len(factory._shot_presenter._projectile_tails))
        self.assertTrue(factory.update_projectile_visual(
            'reused:1', (8.0, 1.1, 0.0), (-80.0, 3.0, 0.0)))

        self.bigworld.run_callbacks()

        self.assertIs(
            replacement,
            factory._shot_presenter._projectile_shots['reused:1'])
        self.assertEqual([replacement['model']], self.bigworld._player.models)
        self.assertEqual({}, factory._shot_presenter._projectile_tails)

    def test_failed_creation_cleanup_keeps_owner_until_exact_retry(self):
        factory = self._factory()
        presenter = factory._shot_presenter
        self.projectile_effects.attach_error = RuntimeError('attach failed')
        self.bigworld._player.del_model_error = RuntimeError(
            'delModel failed')

        self.assertFalse(self._launch(
            factory, projectile_id='retry:creation'))

        retained = presenter._projectile_shots['retry:creation']
        self.assertTrue(retained['creation_failed'])
        self.assertEqual(1, len(self.bigworld._player.models))

        self.projectile_effects.attach_error = None
        self.bigworld._player.del_model_error = None
        self.assertTrue(self._launch(
            factory, projectile_id='retry:creation'))

        active = presenter._projectile_shots['retry:creation']
        self.assertFalse(active['creation_failed'])
        self.assertEqual(1, len(self.bigworld._player.models))
        self.assertEqual(2, len(self.projectile_effects.attach_calls))

    def test_reset_releases_epoch_visuals_and_presenter_can_launch_again(self):
        factory = self._factory()
        self._launch(factory, projectile_id='old:1')
        self._launch(factory, projectile_id='old:2', attacker_id=99)

        self.assertTrue(factory.reset_projectile_visuals())

        presenter = factory._shot_presenter
        self.assertEqual({}, presenter._projectile_shots)
        self.assertEqual({}, presenter._visual_admissions)
        self.assertEqual([], self.bigworld._player.models)
        self.assertTrue(self._launch(factory, projectile_id='new:1'))
        self.assertEqual(1, len(self.bigworld._player.models))

    def test_failed_epoch_reset_disables_later_cosmetic_launches(self):
        factory = self._factory()
        self._launch(factory, projectile_id='old:1')
        presenter = factory._shot_presenter
        self.bigworld._player.del_model_error = RuntimeError(
            'delModel failed')

        self.assertFalse(factory.reset_projectile_visuals())
        self.assertFalse(presenter._launches_enabled)

        self.bigworld._player.del_model_error = None
        self.assertFalse(self._launch(factory, projectile_id='new:1'))
        self.assertNotIn('new:1', presenter._projectile_shots)

    def test_canonical_combat_equipment_artillery_fails_closed(self):
        artillery, unused_effects = _effects(self.events, artillery=True)
        forged = dict(self.effects)
        forged['artilleryID'] = 92
        factory = self._factory()
        presenter = factory._shot_presenter

        with mock.patch('sys.stdout', new_callable=io.StringIO) as output:
            first = self._launch(
                factory, projectile_id='artillery:1', effects=artillery)
            forged_result = self._launch(
                factory, projectile_id='artillery:forged', effects=forged)

        self.assertFalse(first)
        self.assertFalse(forged_result)
        self.assertEqual([], self.bigworld._player.models)
        self.assertEqual([], _ProjectileMover.instances)
        self.assertEqual({}, presenter._projectile_shots)
        self.assertNotIn('artillery:1', presenter._visual_admissions)
        self.assertNotIn(
            'artillery:forged', presenter._visual_admissions)
        self.assertEqual(1, output.getvalue().count(
            'canonical combat-equipment artillery rejected'))
        self.assertFalse(factory.update_projectile_visual(
            'artillery:1', (9.0, 8.0, 7.0)))
        self.assertFalse(factory.stop_projectile_tracer(
            'artillery:1', (9.0, 8.0, 7.0)))

    def test_world_explosion_failure_does_not_disable_controlled_tracers(self):
        factory = self._factory()
        self._launch(factory, projectile_id='world:failed')
        _ProjectileMover.explode_error = RuntimeError('explosion failed')
        with mock.patch.dict(sys.modules, _modules(self.effects)):
            self.assertTrue(factory.stop_projectile_tracer(
                'world:failed', (12.0, 0.0, 3.0),
                explosion=(self.effects, 'ground', (1.0, -1.0, 0.0)),
                missed=True))

        self.assertTrue(self._launch(factory, projectile_id='next:shot'))
        self.assertEqual(2, len(self.bigworld._player.models))
        self.bigworld.run_callbacks()
        self.assertEqual(1, len(self.bigworld._player.models))
        self.assertEqual([], _ProjectileMover.instances[0].calls)
        self.assertEqual(['player-shot-missed'], self.triggers.triggers)

    def test_invalid_launches_fail_before_scene_or_mover_acquisition(self):
        factory = self._factory()
        with mock.patch.dict(sys.modules, _modules(self.effects)):
            self.assertFalse(factory.play_projectile_tracer(
                _descriptor(), 0, (float('nan'), 0.0, 0.0),
                (1.0, 0.0, 0.0), 1.0, 10.0, 1, 'bad:point'))
            self.assertFalse(factory.play_projectile_tracer(
                _descriptor(), 0, (0.0, 0.0, 0.0),
                (1.0, 0.0, 0.0), 1.0, 10.0, 1, None))

        self.assertEqual([], self.bigworld._player.models)
        self.assertEqual([], _ProjectileMover.instances)

    def test_destroy_releases_controlled_and_lazy_native_owners_once(self):
        factory = self._factory()
        self._launch(factory, projectile_id='world:active')
        with mock.patch.dict(sys.modules, _modules(self.effects)):
            mover = factory._shot_presenter._projectile_mover()

        factory.destroy_all()

        self.assertEqual([], self.bigworld._player.models)
        self.assertEqual(1, mover.destroy_calls)
        self.assertFalse(self._launch(factory, projectile_id='late:shot'))

    def test_failed_mover_destroy_retains_owner_for_exact_retry(self):
        factory = self._factory()
        with mock.patch.dict(sys.modules, _modules(self.effects)):
            mover = factory._shot_presenter._projectile_mover()
        _ProjectileMover.destroy_error = RuntimeError('destroy failed')

        with self.assertRaisesRegex(RuntimeError, 'destroy failed'):
            factory.destroy_all()

        self.assertIs(mover, factory._shot_presenter._mover)
        self.assertEqual(1, mover.destroy_calls)
        _ProjectileMover.destroy_error = None
        factory.destroy_all()
        self.assertEqual(2, mover.destroy_calls)
        self.assertIsNone(factory._shot_presenter._mover)

    def test_both_factory_implementations_expose_controlled_visual_lifecycle(self):
        for factory_type in (RemoteVehicleFactory,
                             NativeRemoteVehicleFactory):
            self.assertTrue(callable(getattr(
                factory_type, 'update_projectile_visual', None)))
            self.assertTrue(callable(getattr(
                factory_type, 'reset_projectile_visuals', None)))


if __name__ == '__main__':
    unittest.main()
