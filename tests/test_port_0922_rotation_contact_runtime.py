"""Both control adapters must respect the same swept hull rotation limit."""
import types
import unittest
from unittest import mock

import test_port_0922_battle_runtime as local
import test_port_0922_bot_runtime as bots


class PlayerRotationContactTests(unittest.TestCase):
    def test_motor_contact_uses_full_descriptor_and_publishes_the_bot_response(self):
        runtime = local._runtime()
        battle = local.BattleRuntime(runtime)
        battle.client = local._Client()
        battle._avatar = runtime.bigworld.avatar
        own_descriptor, peer_descriptor = local._Descriptor(), local._Descriptor()
        own_descriptor.physics.update(weight=100000., enginePower=2000.*735.49875)
        peer_descriptor.physics.update(weight=10000., enginePower=300.*735.49875)
        entity = local._Vehicle(10, own_descriptor, local._Vector(), (0, 0, 0), {'health': 500})
        peer = local._Vehicle(11, peer_descriptor, local._Vector(), (0, 0, 0), {'health': 500})
        runtime.bigworld.entities.update({10: entity, 11: peer})
        shape = battle._collision_shape(own_descriptor)
        battle._records['bot:11'] = dict(engine_id=11, network_id=11, kind='bot', ready=True,
            state=dict(id=11, x=2.*shape[0], y=0., z=0., yaw=0., speed=0., team=2,
                       alive=True, rotation_dir=1, movement_dir=0, collision_shape=shape))
        battle._local_physics = local.vehicle_physics.derive_params(own_descriptor)
        battle._sender = types.SimpleNamespace(forward=0.)
        battle._local_drive_turn = 1.
        battle._motion_is_clear = lambda *args, **kw: True
        battle._baked_pose_safe = lambda *args: True
        candidates = battle._contact_tanks((0., 0., 0.), shape, .04)
        self.assertGreater(candidates[0]['traverse_torque'], 0.)
        battle._records['bot:11']['state']['rotation_dir'] = 0
        battle._resolve_local_tank_contacts(entity, (0., 0., 0.), 0., .04)
        sent = battle._local_contact_pushes[11][:]
        self.assertGreater(sent[2], 0.)
        self.assertEqual(0., sent[3])
        battle._records['bot:11']['state']['x'] = 100.
        battle._resolve_local_tank_contacts(entity, (0., 0., 0.), 0., .04)
        self.assertEqual(sent, battle._local_contact_pushes[11])

    def test_side_contact_limits_both_turn_signs_and_releases_after_separation(self):
        for direction in (-1., 1.):
            with self.subTest(direction=direction):
                runtime = local._runtime()
                battle = local.BattleRuntime(runtime)
                battle.client = local._Client()
                battle._avatar = runtime.bigworld.avatar
                entity = local._Vehicle(10, local._Descriptor(), local._Vector(),
                                        (0, 0, 0), {'health': 500})
                runtime.bigworld.entities[10] = entity
                battle._server = types.SimpleNamespace(vehicle_id=10)
                battle._sender = types.SimpleNamespace(
                    forward=0., turn=direction, handbrake=False,
                    send_current=mock.Mock(return_value=True))
                battle._local_descriptor = entity.typeDescriptor
                battle._attach_local_presentation()
                shape = battle._collision_shape(entity.typeDescriptor)
                peer = dict(id=1000001, x=2*shape[0], y=0., z=0.,
                            yaw=0., shape=shape)
                battle._contact_tanks = lambda *args, **kwargs: [peer]
                battle._smoothed_drive_pitch = lambda *args: 0.
                battle._update_vertical_motion = lambda e, p, y, dt: p
                battle._ground_pitch = lambda *args: 0.
                battle._apply_slope_slide = lambda p, y, dt, e=None: p
                battle._resolve_local_tank_contacts = lambda e, p, y, dt: p
                with mock.patch.object(local.vehicle_physics, 'longitudinal_step', return_value=0.), \
                        mock.patch.object(local.vehicle_physics, 'traverse_step', return_value=2*direction):
                    battle._drive_local(.1)
                    stopped = battle._local_yaw
                    self.assertGreater(stopped*direction, 0.)
                    self.assertLess(abs(stopped), .011/shape[1])
                    self.assertEqual(0., battle._local_turn_speed)
                    peer['x'] = 100.
                    battle._drive_local(.1)
                self.assertAlmostEqual(.2*direction, battle._local_yaw-stopped)


class BotRotationContactTests(unittest.TestCase):
    setUp = bots.BotRuntimeTests.setUp
    tearDown = bots.BotRuntimeTests.tearDown

    def test_traffic_keeps_supplied_neighbour_coordinates_for_both_wire_shapes(self):
        runtime = self.module.BotRuntime(1)
        bodies, unused_index = runtime._traffic_snapshot([
            dict(id=1000001, position=(20., 3., -12.), team=2),
            dict(id=1000002, x=-18., y=2., z=35., team=2)])
        self.assertEqual((20., 3., -12.), bodies[1000001]['position'])
        self.assertEqual((-18., 2., 35.), bodies[1000002]['position'])

    def test_player_side_contact_limits_both_bot_turn_signs_and_releases(self):
        for direction in (-1., 1.):
            with self.subTest(direction=direction):
                command = bots.BotRuntimeTests._stationary_command()
                command.update(turn=direction, target_yaw=direction,
                               movement_intent=True, recovery_mode='drive')
                runtime = self.module.BotRuntime(
                    1, descriptor_resolver=lambda unused: bots._combat_descriptor(),
                    adapter_factory=lambda *args, **kwargs: bots._FixedAdapter(command),
                    direction_probe=lambda *args: {'clear': True, 'slope': 0.},
                    ground_probe=lambda *args: 0., physics_ground_probe=lambda *args: 0.,
                    spawn_resolver=bots._spawn_resolver, baked_graph=bots._graph())
                runtime.battle_start(self.start)
                state = runtime.states[11]
                state.update(x=0., y=0., z=0., yaw=0., speed=0., grounded_once=True)
                shape = state['collision_shape']
                peer = dict(id=1, x=2*shape[0], y=0., z=0., yaw=0.,
                            half_width=shape[0], half_length=shape[1],
                            shape=shape, mass=state['mass'], speed=0., alive=True)
                with mock.patch.object(self.module.vehicle_physics, 'longitudinal_step', return_value=0.), \
                        mock.patch.object(self.module.vehicle_physics, 'traverse_step', return_value=2*direction):
                    runtime.update(.04, 1., neighbours=[peer])
                    stopped = state['yaw']
                    self.assertGreater(stopped*direction, 0.)
                    self.assertLess(abs(stopped), .011/shape[1])
                    self.assertEqual(0., runtime._turn_speeds[11])
                    # The physical yaw sweep still clips the forced native
                    # motion above, while AI withdraws steering torque into
                    # the player rather than continuing to push their side.
                    self.assertEqual(0, state['rotation_dir'])
                    peer['x'] = 100.
                    runtime.update(.04, 1.04, neighbours=[peer])
                self.assertAlmostEqual(.08*direction, state['yaw']-stopped)
