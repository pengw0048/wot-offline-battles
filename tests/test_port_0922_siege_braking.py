import copy
import math
import types
import unittest
from unittest import mock

import test_port_0922_battle_runtime as local_fixture
import test_port_0922_bot_runtime as bot_fixture
import test_port_0922_server_projectiles as server_fixture
from gui.mods.offline_lan_0922 import siege_mechanics


def local_battle(vehicle_name, current, speed, turn=0.0):
    runtime = local_fixture._runtime()
    battle = local_fixture.BattleRuntime(runtime)
    battle.client = local_fixture._Client()
    battle._avatar = runtime.bigworld.avatar
    descriptor = local_fixture._Descriptor(vehicle_name)
    descriptor.hasSiegeMode = True
    if current == siege_mechanics.ENABLED:
        limit = siege_mechanics.enabled_speed_limit(vehicle_name)
        descriptor.physics['speedLimits'] = (limit, limit)
    entity = local_fixture._Vehicle(
        10, descriptor, local_fixture._Vector(), (0, 0, 0), {'health': 500})
    entity.siegeState = current
    for name in ('bodyMatrix', 'groundPlacingMatrix',
                 'groundPlacingMatrixFiltered', 'stabilisedMatrix'):
        setattr(entity.filter, name, local_fixture._Matrix())
    entity.filter.getVehiclePhysics = lambda: types.SimpleNamespace(
        setHullAimingAnglesDelta=mock.Mock())
    runtime.bigworld.entities[10] = entity
    battle._server = types.SimpleNamespace(vehicle_id=10)
    battle._local_descriptor = descriptor
    battle._sender = local_fixture._LANInputSender(battle)
    battle._sender.forward = 1.0 if speed >= 0.0 else -1.0
    battle._sender.turn = 1.0
    battle._attach_local_presentation()
    battle._local_speed = speed
    battle._local_turn_speed = turn
    battle._smoothed_drive_pitch = lambda *unused: 0.0
    battle._motion_is_clear = lambda *unused, **unused_kw: True
    battle._update_vertical_motion = lambda entity, position, yaw, dt: position
    return battle, entity


def mode_inputs(battle):
    return [row[2] for row in battle.client.sent
            if row[0] == 'input' and 'siege_enabled' in row[2]]


class LocalSiegeBrakingTests(unittest.TestCase):
    def test_both_modes_and_travel_directions_brake_before_requesting(self):
        for vehicle in siege_mechanics.VEHICLE_PARAMS:
            for current in (siege_mechanics.DISABLED, siege_mechanics.ENABLED):
                for sign in (-1.0, 1.0):
                    with self.subTest(vehicle=vehicle, state=current, sign=sign):
                        speed = sign * (1.2 if current == 2 else 8.0)
                        battle, entity = local_battle(vehicle, current, speed)
                        setting = battle._runtime.constants.VEHICLE_SETTING.SIEGE_MODE_ENABLED
                        enabled = current == siege_mechanics.DISABLED
                        self.assertTrue(battle.change_vehicle_setting(setting, enabled))
                        self.assertEqual(speed, battle._local_speed)
                        self.assertEqual([], mode_inputs(battle))
                        self.assertIsNone(battle._local_siege_pending)
                        before = battle._local_position
                        battle._drive_local(0.04)
                        self.assertGreater(abs(battle._local_speed), 0.0)
                        self.assertLess(abs(battle._local_speed), abs(speed))
                        self.assertNotEqual(before, battle._local_position)
                        for unused in range(100):
                            if mode_inputs(battle):
                                break
                            self.assertEqual(current, entity.siegeState)
                            self.assertIsNone(battle._local_siege_pending)
                            battle._drive_local(0.04)
                        self.assertEqual(0.0, battle._local_speed)
                        self.assertEqual(1, len(mode_inputs(battle)))
                        self.assertEqual(enabled, mode_inputs(battle)[0]['siege_enabled'])
                        self.assertEqual(0.0, mode_inputs(battle)[0]['speed'])
                        self.assertIsNone(battle._local_siege_braking)
                        self.assertEqual(enabled, battle._local_siege_pending[0])
                        self.assertTrue(all(row[1][:2] == (0.0, 0.0)
                                            for row in battle.client.sent
                                            if row[0] == 'input'))

    def test_track_direction_is_shared_by_every_siege_vehicle_and_both_modes(self):
        for name in siege_mechanics.VEHICLE_PARAMS:
            for current in (0, 2):
                for speed in (-0.8, 0.0, 0.8):
                    for turn in (-0.4, 0.4):
                        with self.subTest(vehicle=name, state=current,
                                          speed=speed, turn=turn):
                            battle, entity = local_battle(name, current, speed, turn)
                            battle._local_drive_turn = turn
                            battle._update_local_tracks(entity)
                            left, right = entity.track_scrolls[-1]
                            self.assertGreater((left - right) * turn, 0.0)
                            remote = local_fixture.RemoteVehicle(
                                1000, entity.typeDescriptor,
                                {'publicInfo': {'team': 2, 'name': 'Bot'},
                                 'health': 500, 'isCrewActive': True,
                                 'gunAnglesPacked': 0},
                                local_fixture._Vector(), (0, 0, 0), battle._runtime.math)
                            remote.attach_track_animation(
                                local_fixture._VehicleFilter(),
                                local_fixture._TrackScroll(), None)
                            battle._remote_factory = types.SimpleNamespace(
                                get=lambda unused: remote, track_animation_error=None)
                            battle._bot_yaw_rates[3] = turn
                            battle._report_bot_tracks = mock.Mock()
                            battle._update_bot_tracks(
                                {'engine_id': 1000, 'kind': 'bot', 'network_id': 3},
                                {'id': 3, 'speed': speed, 'alive': True,
                                 'health': 500, 'siege_state': current}, 10.0)
                            self.assertEqual((left, right), remote.track_scroll.external)

    def test_pivot_must_settle_before_sending_a_mode_request(self):
        battle, entity = local_battle('sweden:S22_Strv_S1', 0, 0.0, 0.5)
        setting = battle._runtime.constants.VEHICLE_SETTING.SIEGE_MODE_ENABLED
        self.assertTrue(battle.change_vehicle_setting(setting, True))
        self.assertEqual([], mode_inputs(battle))
        battle._drive_local(0.1)
        self.assertEqual(0.0, battle._local_turn_speed)
        self.assertEqual(1, len(mode_inputs(battle)))

    def test_returning_to_the_current_mode_cancels_braking(self):
        for current in (0, 2):
            battle, entity = local_battle('sweden:S22_Strv_S1', current, 1.0)
            setting = battle._runtime.constants.VEHICLE_SETTING.SIEGE_MODE_ENABLED
            self.assertTrue(battle.change_vehicle_setting(setting, current == 0))
            self.assertTrue(battle.change_vehicle_setting(setting, current == 2))
            self.assertIsNone(battle._local_siege_braking)
            self.assertEqual(1.0, battle._local_speed)
            self.assertEqual([], mode_inputs(battle))

    def test_airborne_motion_cannot_be_erased_to_start_switching(self):
        battle, entity = local_battle('sweden:S22_Strv_S1', 0, 8.0)
        battle._local_airborne = True
        setting = battle._runtime.constants.VEHICLE_SETTING.SIEGE_MODE_ENABLED
        self.assertTrue(battle.change_vehicle_setting(setting, True))
        battle._drive_local(0.1)
        self.assertEqual(8.0, battle._local_speed)
        self.assertEqual([], mode_inputs(battle))

    def test_failed_enqueue_retries_stopped_and_death_cancels_braking(self):
        battle, entity = local_battle('sweden:S22_Strv_S1', 0, 0.2)
        setting = battle._runtime.constants.VEHICLE_SETTING.SIEGE_MODE_ENABLED
        battle.change_vehicle_setting(setting, True)
        with mock.patch.object(battle._sender, 'send_current', return_value=False):
            battle._drive_local(0.1)
        self.assertEqual(0.0, battle._local_speed)
        self.assertIs(battle._local_siege_braking, True)
        self.assertIsNone(battle._local_siege_pending)
        battle._drive_local(0.1)
        self.assertEqual(1, len(mode_inputs(battle)))
        battle, entity = local_battle('sweden:S22_Strv_S1', 0, 8.0)
        battle.change_vehicle_setting(setting, True)
        entity.health = 0
        battle._drive_local(0.1)
        self.assertIsNone(battle._local_siege_braking)
        self.assertEqual([], mode_inputs(battle))


class ServerSiegeBrakingTests(unittest.TestCase):
    def test_request_uses_the_current_packet_speed_and_never_stops_a_moving_tank(self):
        for vehicle in siege_mechanics.VEHICLE_PARAMS:
            for current in (0, 2):
                for speed in (-1.0, 1.0):
                    with self.subTest(vehicle=vehicle, state=current, speed=speed):
                        state = server_fixture._state()
                        player = state.players[1]
                        player.vehicle = vehicle
                        player.siege_state = current
                        server_fixture._update_player_input(
                            state, 1, siege_enabled=current == 0, speed=speed)
                        self.assertEqual(current, player.siege_state)
                        self.assertEqual(speed, player.speed)
                        self.assertEqual(0, player.siege_transition_ticks)
                        server_fixture._update_player_input(
                            state, 1, siege_enabled=current == 0, speed=0.0)
                        self.assertEqual(1 if current == 0 else 3, player.siege_state)
                        expected = siege_mechanics.transition_seconds(vehicle, current == 0)
                        self.assertEqual(round(expected * server_fixture.TICK_HZ),
                                         player.siege_transition_ticks)


class BotSiegeBrakingTests(unittest.TestCase):
    def setUp(self):
        self.harness = bot_fixture.BotRuntimeTests()
        self.harness.setUp()
        self.module = self.harness.module

    def tearDown(self):
        self.harness.tearDown()

    def test_each_mode_uses_its_installed_directional_downhill_limits(self):
        for current in (0, 2):
            for sign in (-1.0, 1.0):
                with self.subTest(state=current, sign=sign):
                    travel = bot_fixture._combat_descriptor()
                    travel.physics['speedLimits'] = (9.0, 4.0)
                    siege = copy.deepcopy(travel)
                    siege.physics['speedLimits'] = (2.0, 1.5)
                    composite = types.SimpleNamespace(
                        hasSiegeMode=True, defaultVehicleDescr=travel,
                        siegeVehicleDescr=siege)
                    command = dict(self.harness._stationary_command(),
                                   throttle=sign, movement_intent=True,
                                   recovery_mode='drive', brake=False)
                    runtime = self.module.BotRuntime(
                        1, descriptor_resolver=lambda unused: composite,
                        adapter_factory=lambda *args: bot_fixture._FixedAdapter(command),
                        direction_probe=lambda *args: {
                            'clear': True, 'slope': -math.tan(math.radians(20.0))},
                        ground_probe=lambda *args: 0.0,
                        physics_ground_probe=lambda *args: 0.0,
                        spawn_resolver=lambda *args: ((0.0, 0.0, 0.0), 0.0),
                        baked_graph=bot_fixture._flat_open_graph())
                    start = copy.deepcopy(self.harness.start)
                    start['bots'][0]['vehicle'] = 'sweden:S22_Strv_S1'
                    runtime.battle_start(start)
                    runtime.navigator = None
                    state = runtime.states[11]
                    runtime._set_bot_siege_state(state, current)
                    runtime._siege_desired = lambda *args: current == 2
                    installed = travel if current == 0 else siege
                    limit = installed.physics['speedLimits'][0 if sign > 0.0 else 1]
                    maximum = limit * self.module.vehicle_physics.OVERSPEED_MAX_FACTOR
                    state.update(speed=sign * (maximum - 0.01), grounded_once=True,
                                 _siege_intent=current == 2)
                    runtime.update(0.1, 1.0)
                    self.assertEqual(current, state['siege_state'])
                    self.assertIs(installed, runtime._descriptors[11])
                    self.assertAlmostEqual(sign * maximum, state['speed'])
                    self.assertGreater(abs(state['speed']), limit)

    def test_both_modes_brake_with_current_descriptor_before_the_transition(self):
        for current in (0, 2):
            for sign in (-1.0, 1.0):
                with self.subTest(state=current, sign=sign):
                    travel = bot_fixture._combat_descriptor()
                    siege = copy.deepcopy(travel)
                    siege.physics['speedLimits'] = (8.0 / 3.6, 8.0 / 3.6)
                    composite = types.SimpleNamespace(
                        hasSiegeMode=True, defaultVehicleDescr=travel,
                        siegeVehicleDescr=siege)
                    command = {'target_yaw': 0.0, 'throttle': sign, 'turn': 0.0,
                               'movement_intent': True, 'shell_index': 0,
                               'fire_allowed': False, 'target_id': None,
                               'fire_range': 500.0}
                    runtime = self.module.BotRuntime(
                        1, descriptor_resolver=lambda unused: composite,
                        adapter_factory=lambda *args: bot_fixture._FixedAdapter(command),
                        direction_probe=lambda *args: {'clear': True, 'slope': 0.0},
                        ground_probe=lambda *args: 0.0,
                        physics_ground_probe=lambda *args: 0.0,
                        spawn_resolver=lambda *args: ((0.0, 0.0, 0.0), 0.0),
                        baked_graph=bot_fixture._flat_open_graph())
                    start = copy.deepcopy(self.harness.start)
                    start['bots'][0]['vehicle'] = 'sweden:S22_Strv_S1'
                    runtime.battle_start(start)
                    state = runtime.states[11]
                    runtime._set_bot_siege_state(state, current)
                    speed = sign * (1.2 if current == 2 else 8.0)
                    state.update(speed=speed, grounded_once=True,
                                 _siege_intent=current == 0, _siege_intent_elapsed=2.0)
                    runtime._siege_desired = lambda *args: current == 0
                    runtime.update(0.04, 1.0)
                    self.assertEqual(current, state['siege_state'])
                    self.assertGreater(abs(state['speed']), 0.0)
                    self.assertLess(abs(state['speed']), abs(speed))
                    self.assertIs(runtime._descriptors[11], travel if current == 0 else siege)
                    for index in range(100):
                        if state['siege_state'] != current:
                            break
                        self.assertEqual(0, state['siege_time_left_ms'])
                        runtime.update(0.04, 1.04 + index * 0.04)
                    self.assertEqual(1 if current == 0 else 3, state['siege_state'])
                    self.assertEqual(0.0, state['speed'])
                    self.assertGreater(state['siege_time_left_ms'], 0)


if __name__ == '__main__':
    unittest.main()
