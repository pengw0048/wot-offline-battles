"""Exercise session ordering with native-style synchronous/pending creation.

These fakes model the factory and readiness guards. They do not prove that the
original #1513 modules or missing native interfaces execute on Windows.
"""

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


ADAPTER = Path(__file__).parents[1] / 'bigworld/res/scripts/client/StandaloneClientSession.py'


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.entities = {}
        self.player = None
        self.fail_vehicle_destroy = False
        self.fail_avatar_create = False
        test = self

        class Entity(object):
            pass

        class Avatar(Entity):
            def __init__(self):
                self.id = 1
                self.spaceID = 99
                self.inWorld = True
                self.playerVehicleID = 0
                self.arena = SimpleNamespace(vehicles={})
                self.inputHandler = SimpleNamespace(_AvatarInputHandler__isStarted=False)
                self._PlayerAvatar__initProgress = 0
                self._PlayerAvatar__isOnArena = False

        class Vehicle(Entity):
            def __init__(self, entity_id):
                # Real beforeCreate runs before the native entity factory.
                test.assertEqual(test.player.playerVehicleID, entity_id)
                test.assertIn(entity_id, test.player.arena.vehicles)
                self.id = entity_id
                self.spaceID = 99
                self.inWorld = False
                self.isStarted = False
                self._Vehicle__isEnteringWorld = False
                self.appearance = None
                test.events.append('vehicle_constructor')
                test.assertIs(self.cell, test.vehicle_server)

        self.avatar_class, self.vehicle_class = Avatar, Vehicle
        bw = ModuleType('BigWorld')
        bw.Entity = Entity
        bw.entity = self.entities.get
        bw.player = lambda: self.player
        for name in ('WGVehicleFilter', 'WGVehiclePhysics', 'CompoundAssembler',
                     'PyTrackScroll', 'PyBallisticsSimulator'):
            setattr(bw, name, type(name, (), {}))

        def create_avatar(space_id, position, direction, properties):
            if self.fail_avatar_create:
                raise RuntimeError('native base creation failed before census publication')
            avatar = Avatar()
            self.assertIs(avatar.base, self.avatar_server)
            self.events.append('avatar_become_player')
            self.player = avatar
            self.entities[avatar.id] = avatar
            return avatar.id

        def create_vehicle(space_id, position, direction, properties, before_create):
            self.events.append('allocate_vehicle_id')
            self.assertIs(before_create(42), True)
            vehicle = Vehicle(42)
            self.entities[42] = vehicle
            return 42

        def destroy_vehicle(space_id, entity_id):
            if self.fail_vehicle_destroy:
                raise RuntimeError('single entity destruction failed')
            self.assertIsNotNone(self.player)
            self.events.append('vehicle_leave_world')
            self.entities.pop(entity_id, None)

        def destroy_avatar(space_id, entity_id):
            # BWNullConnection's base reset clears pending Vehicles first.
            if 42 in self.entities:
                self.events.append('reset_pending_vehicle')
                del self.entities[42]
            self.events.append('avatar_become_nonplayer')
            self.entities.pop(entity_id, None)
            self.player = None

        bw.standaloneCreateSessionAvatar = create_avatar
        bw.standaloneCreateSessionVehicle = create_vehicle
        bw.standaloneDestroySessionVehicle = destroy_vehicle
        bw.standaloneDestroySessionAvatar = destroy_avatar
        avatar_module = ModuleType('Avatar')
        avatar_module.Avatar = avatar_module.PlayerAvatar = Avatar
        avatar_module._INIT_STEPS = SimpleNamespace(INIT_COMPLETED=16)
        vehicle_module = ModuleType('Vehicle')
        vehicle_module.Vehicle = Vehicle
        vehicle_systems = ModuleType('vehicle_systems')
        vehicle_systems.CompoundAppearance = ModuleType('CompoundAppearance')
        modules = {'BigWorld': bw, 'Avatar': avatar_module,
                   'Vehicle': vehicle_module, 'vehicle_systems': vehicle_systems,
                   'AvatarInputHandler': ModuleType('AvatarInputHandler'),
                   'ProjectileMover': ModuleType('ProjectileMover')}
        self.modules = patch.dict(sys.modules, modules)
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.adapter = ModuleType('session_under_test')
        exec(compile(ADAPTER.read_bytes(), str(ADAPTER), 'exec'), self.adapter.__dict__)
        self.session = self.adapter.ClientSession()
        self.avatar_server, self.vehicle_server = object(), object()
        self.bw = bw

    def begin(self):
        self.assertEqual(self.session.begin(99, {}, (0, 0, 0), (0, 0, 0),
                                            self.avatar_server), 1)

    def select(self, entity_id):
        self.events.append('publish_roster_and_select')
        self.player.arena.vehicles[entity_id] = {'id': entity_id}
        self.player.playerVehicleID = entity_id

    def spawn(self):
        return self.session.spawnOwnVehicle({}, (0, 0, 0), (0, 0, 0),
                                             self.vehicle_server, self.select)

    def test_identity_precedes_synchronous_constructor_and_readiness_is_not_fabricated(self):
        self.begin()
        self.assertEqual(self.spawn(), 42)
        self.assertLess(self.events.index('publish_roster_and_select'),
                        self.events.index('vehicle_constructor'))
        self.assertEqual(self.session.advance(), 'vehicle_loading')
        vehicle = self.entities[42]
        vehicle.inWorld = vehicle.isStarted = True
        vehicle.appearance = object()
        self.assertEqual(self.session.advance(), 'battle_loading')
        self.player._PlayerAvatar__initProgress = 16
        self.player.inputHandler._AvatarInputHandler__isStarted = True
        self.assertEqual(self.session.advance(), 'battle_loading')
        self.assertFalse(self.player._PlayerAvatar__isOnArena)
        self.player._PlayerAvatar__isOnArena = True
        self.assertEqual(self.session.advance(), 'active')
        self.session.stop()

    def test_selector_failure_never_constructs_vehicle_and_partial_stop_is_repeatable(self):
        self.begin()
        with self.assertRaisesRegex(RuntimeError, 'did not select'):
            self.session.spawnOwnVehicle({}, (0, 0, 0), (0, 0, 0),
                                         self.vehicle_server, lambda entity_id: None)
        self.assertNotIn('vehicle_constructor', self.events)
        self.assertEqual(self.session.stage, 'failed')
        self.assertTrue(self.session.stop())
        self.assertTrue(self.session.stop())
        self.assertIsNone(self.adapter._ACTIVE_SESSION)
        self.assertNotIn('base', self.avatar_class.__dict__)
        self.assertNotIn('cell', self.vehicle_class.__dict__)

    def test_begin_failure_before_native_publication_releases_global_owner(self):
        self.fail_avatar_create = True
        with self.assertRaisesRegex(RuntimeError, 'before census'):
            self.begin()
        self.assertTrue(self.session.stop())
        self.assertTrue(self.session.stop())
        self.assertIsNone(self.adapter._ACTIVE_SESSION)
        self.assertEqual(self.entities, {})

    def test_pending_vehicle_cleanup_uses_avatar_reset_after_single_destroy_failure(self):
        self.begin()
        self.spawn()
        self.fail_vehicle_destroy = True
        with self.assertRaisesRegex(RuntimeError, 'Vehicle teardown'):
            self.session.stop()
        self.assertIn('reset_pending_vehicle', self.events)
        self.assertIsNone(self.adapter._ACTIVE_SESSION)
        self.assertEqual(self.entities, {})
        self.assertTrue(self.session.stop())

    def test_missing_native_interface_blocks_before_entity_construction(self):
        del self.bw.PyBallisticsSimulator
        with self.assertRaisesRegex(RuntimeError, 'PyBallisticsSimulator'):
            self.begin()
        self.assertEqual(self.events, [])
        self.assertEqual(self.session.stage, 'failed')
        self.assertTrue(self.session.stop())


if __name__ == '__main__':
    unittest.main()
