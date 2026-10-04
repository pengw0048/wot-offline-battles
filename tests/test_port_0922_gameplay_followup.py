"""Regressions for the follow-up reported after the first playable test build."""
import math
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src/res/scripts/client'))
sys.path.insert(0, str(ROOT / 'server'))

from gui.mods.offline_lan_0922 import battle_shell_tooltip
from gui.mods.offline_lan_0922.ai import driver
from gui.mods.offline_lan_0922.artillery_controller import ArtilleryController
from gui.mods.offline_lan_0922.authority_worker import AuthorityWorkerLANClient
from gui.mods.offline_lan_0922.lan_client import LANClient
from lan_battle_server import BattleState, PREBATTLE_SECONDS, TICK_HZ
from lan_battle_server import SIMULATION_WORKER_AUTHORITY_ID as WORKER
import test_port_0922_bot_runtime as bot_fixture
import test_port_0922_server_projectiles as shot_fixture
from test_port_0922_postbattle import _receipt, _packed_vehicle


class ResultAndTimingTests(unittest.TestCase):
    def test_all_stock_detail_fields_survive_receipt_packing(self):
        receipt = _receipt()
        receipt['stats'].update(BattleState._receipt_statistics({
            'explosion_hits': 3, 'explosion_hits_received': 4,
            'damaged': 5, 'kills': 2,
            'sniper_damage_dealt': 780, 'team_hits': 2,
            'team_damage': 85, 'team_kills': 1,
            'mileage': 1234.4, 'life_time': 127}))
        receipt['public_results'][0]['stats'] = dict(receipt['stats'])
        full = _packed_vehicle(receipt)
        expected = {
            'explosionHits': 3, 'explosionHitsReceived': 4,
            'damaged': 5, 'kills': 2,
            'sniperDamageDealt': 780, 'directTeamHits': 2,
            'tdamageDealt': 85, 'tkills': 1,
            'mileage': 1234, 'lifeTime': 127}
        self.assertEqual(expected, {key: full[key] for key in expected})

    def test_real_he_resolution_records_splash_once(self):
        state = shot_fixture._state(players=4)
        self.assertTrue(shot_fixture._launch_authority(
            state, shot_fixture._launch(
                is_he=True, splash_radius=20.0, penetration_factor=0.0)))
        message = shot_fixture._resolve(
            '1:p:1:1', penetration_factor=0.0,
            direct=shot_fixture._effect(damage=50, shot_result=1),
            splash=[shot_fixture._effect(
                target_id=4, damage=40, x=10.0,
                target_pose=(30.0, 1.0, 0.0))])
        self.assertTrue(state.resolve_projectile(WORKER, message))
        self.assertEqual(2, state._statistics_row('player', 1)['explosion_hits'])
        self.assertEqual(90, state._statistics_row('player', 1)['damage_dealt'])
        self.assertEqual(2, state._statistics_row('player', 1)['damaged'])
        self.assertEqual(1, state._statistics_row(
            'player', 2)['explosion_hits_received'])
        self.assertEqual(1, state._statistics_row(
            'player', 4)['explosion_hits_received'])
        state.resolve_projectile(WORKER, message)
        self.assertEqual(2, state._statistics_row('player', 1)['explosion_hits'])

    def test_damaged_counts_distinct_enemies_for_any_accepted_damage_cause(self):
        state = shot_fixture._state(players=4)
        for victim, amount in ((2, 10), (2, 30), (4, 40), (3, 50), (4, 0)):
            state._record_damage(('player', 1), ('player', victim), amount, {})
        stats = state._receipt_statistics(state._statistics_row('player', 1))
        self.assertEqual((2, 80, 50), (
            stats['damaged'], stats['damage'], stats['team_damage']))
        receipt = _receipt()
        receipt['stats'].update(stats)
        receipt['stats']['kills'] = 1
        receipt['public_results'][0]['stats'] = dict(receipt['stats'])
        packed = _packed_vehicle(receipt)
        self.assertEqual((2, 1), (packed['damaged'], packed['kills']))

    def test_penetrating_he_and_zero_damage_direct_he_are_not_splash_damage(self):
        for damage, result in ((100, 2), (0, 1)):
            with self.subTest(damage=damage, result=result):
                state = shot_fixture._state()
                self.assertTrue(shot_fixture._launch_authority(
                    state, shot_fixture._launch(is_he=True, splash_radius=2.0)))
                self.assertTrue(state.resolve_projectile(
                    WORKER, shot_fixture._resolve('1:p:1:1',
                        direct=shot_fixture._effect(damage=damage, shot_result=result))))
                row = state._statistics_row('player', 2)
                self.assertEqual(0, row['explosion_hits_received'])
                self.assertEqual(1, row['hits_received'])

    def test_friendly_damage_and_death_use_accepted_health_and_combat_tick(self):
        state = shot_fixture._state()
        state.players[2].team = 1
        state.players[2].health = 40
        state.tick += int(127 * TICK_HZ)
        self.assertTrue(shot_fixture._launch_authority(state, shot_fixture._launch()))
        self.assertTrue(state.resolve_projectile(
            WORKER, shot_fixture._resolve('1:p:1:1')))
        actor = state._statistics_row('player', 1)
        self.assertEqual((1, 40, 1), (
            actor['team_hits'], actor['team_damage'], actor['team_kills']))
        self.assertEqual(0, actor['damage_dealt'])
        state.tick += int(60 * TICK_HZ)
        state._finalize_vehicle_statistics()
        self.assertEqual(127, state._statistics_row('player', 2)['life_time'])
        self.assertEqual(187, state._statistics_row('player', 1)['life_time'])

    def test_mileage_records_input_segments_including_reverse_but_not_spawn(self):
        state = shot_fixture._state()
        player = state.players[1]
        player.client_position = False
        for x, z in ((100, 100), (103, 104), (100, 100)):
            state.tick += 1
            self.assertTrue(shot_fixture._update_player_input(state, 1, x=x, z=z))
        self.assertAlmostEqual(10.0, state._statistics_row('player', 1)['mileage'])
        state.battle_result = {'winner': 1}
        state._record_vehicle_travel('player', 1, (0, 0, 0), (100, 0, 0))
        self.assertAlmostEqual(10.0, state._statistics_row('player', 1)['mileage'])

    def test_hidden_fps_is_inverted_and_rtt_does_not_replace_frame_time(self):
        worker = AuthorityWorkerLANClient('localhost', 28782)
        worker.round_id, worker.authority_epoch = 1, 2
        for fps, expected in ((50, 20), (10, 100), (5, 200)):
            worker.authority_epoch += 1
            for frame in range(fps):
                worker.record_frame_interval(1.0 / fps, now=10.0)
            self.assertAlmostEqual(expected, worker.frame_latency_ms(now=10.0))
        self.assertGreaterEqual(worker.frame_latency_ms(now=10.7), 699.0)
        worker.authority_epoch += 1
        self.assertIsNone(worker.frame_latency_ms(now=11.0))

        client = LANClient('localhost', 28782, 'Test', 'ussr:R11_MS-1')
        client.connected = True
        client.round_id, client.authority_epoch = 1, 2
        client._handle_message({
            'type': 'worker_pong', 'round_id': 1, 'authority_epoch': 2,
            'seq': 1, 'client_time': 10.0, 'frame_ms': 100.0,
            '_client_received_time': 10.25})
        self.assertEqual((100, False), client.worker_ping_display(10.3))
        self.assertEqual(250.0, client.worker_rtt_ms)
        self.assertEqual((999, True), client.worker_ping_display(14.0))

    def test_speed_reuses_native_font_spans_separator_and_number_formatter(self):
        shell = types.SimpleNamespace(compactDescr=1)
        vehicle = types.SimpleNamespace(gun=types.SimpleNamespace(
            shots=[types.SimpleNamespace(shell=shell, speed=1120.0)]))
        body = ('<font color="#AABBCC">Damage: </font>'
                '<font color="#FFFFFF">500</font><br/>'
                '<font color="#AABBCC">Penetration: </font>'
                '<font color="#FFFFFF">200</font>')
        text = '{HEADER}HE{/HEADER}\n/{BODY}' + body + '{/BODY}'
        result = battle_shell_tooltip.append_speed(
            text, shell, vehicle, lambda value: format(value, ','))
        expected = ('<br/><font color="#AABBCC">'
                    '\u70ae\u5f39\u901f\u5ea6 (\u7c73/\u79d2): </font>'
                    '<font color="#FFFFFF">1,120</font>')
        self.assertIn(expected + '{/BODY}', result)
        self.assertIn(body, result)
        self.assertEqual(1, result.count('{BODY}'))


    def test_reference_shell_rows_use_current_shot_values_and_inline_units(self):
        cases = (
            ('ARMOR_PIERCING_CR', '\u5408\u91d1\u7a7f\u7532\u5f39',
             600, (262, 252), 1350, '262-252', None),
            ('HIGH_EXPLOSIVE', '\u9ad8\u7206\u5f39',
             510, (53, 53), 1025, '53', 1.91),
            ('ARMOR_PIERCING', '\u7a7f\u7532\u5f39',
             390, (258, 232), 1241, '258-232', None),
            ('HOLLOW_CHARGE', '\u9ad8\u7206\u53cd\u5766\u514b\u5f39',
             600, (325, 325), 900, '325', None),
        )
        stock = ('{HEADER}old{/HEADER}\n/{BODY}'
                 '<font color="#FFFFFF">Damage: 1</font><br/>'
                 '<font color="#FFFFFF">Penetration: 2</font>{/BODY}')
        for kind, title, damage, piercing, speed, power, radius in cases:
            with self.subTest(kind=kind):
                shell = types.SimpleNamespace(
                    compactDescr=42, kind=kind, damage=(damage, 100),
                    type=types.SimpleNamespace(explosionRadius=radius))
                # Native NoLegacyStuff objects forbid every dict-like operation.
                class GunShot:
                    def get(self, *args):
                        raise AssertionError('NoLegacyStuff.get')
                shot = GunShot()
                shot.shell, shot.speed, shot.piercingPower = shell, speed, piercing
                vehicle = types.SimpleNamespace(gun=types.SimpleNamespace(
                    shots=[types.SimpleNamespace(shell=object(), speed=1), shot]))
                text = battle_shell_tooltip.append_speed(stock, shell, vehicle)
                plain = __import__('re').sub(r'<[^>]*>', '', text)
                self.assertIn('{HEADER}' + title + '{/HEADER}', plain)
                self.assertIn('\u5e73\u5747\u4f24\u5bb3: %s\u70b9' % damage, plain)
                self.assertIn('\u5e73\u5747\u7a7f\u6df1: %s\u6beb\u7c73*' % power, plain)
                self.assertIn('\u70ae\u5f39\u901f\u5ea6: %s\u7c73/\u79d2' %
                              format(speed, ','), plain)
                if radius is None:
                    self.assertNotIn('\u4f24\u5bb3\u534a\u5f84', plain)
                else:
                    self.assertIn('\u4f24\u5bb3\u534a\u5f84: 1.91\u7c73', plain)
                note = ('*\u53d7\u8ddd\u79bb\u5f71\u54cd: 50 - 500\u7c73'
                        if kind.startswith('ARMOR_PIERCING') else
                        '*\u6b64\u7c7b\u578b\u70ae\u5f39\u4e0d\u53d7\u8ddd\u79bb\u5f71\u54cd')
                self.assertIn(note, plain)
                self.assertEqual(1, text.count('{BODY}'))
                self.assertEqual(text.count('<font'), text.count('</font>'))
                self.assertEqual(text.encode('utf-8'),
                    battle_shell_tooltip.append_speed(stock.encode('utf-8'), shell, vehicle))

    def test_reference_shell_preserves_stock_stun_row_and_uses_installed_ammo(self):
        shell = types.SimpleNamespace(
            compactDescr=8, kind='HIGH_EXPLOSIVE', damage=(731, 200),
            type=types.SimpleNamespace(explosionRadius=3.2))
        vehicle = types.SimpleNamespace(gun=types.SimpleNamespace(shots=[
            types.SimpleNamespace(shell=shell, speed=602, piercingPower=(81, 81))]))
        stock = ('{HEADER}HE{/HEADER}\n/{BODY}Damage: 731<br/>'
                 'Penetration: 81<br/>Stun: 12-20{/BODY}')
        text = battle_shell_tooltip.append_speed(stock, shell, vehicle)
        self.assertIn('731\u70b9', text)
        self.assertIn('602\u7c73/\u79d2', text)
        self.assertIn('3.2\u7c73', text)
        self.assertIn('Stun: 12-20', text)


class AimProgressTests(unittest.TestCase):
    def setUp(self):
        self.fixture = bot_fixture.BotRuntimeTests()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_limited_turret_crosses_the_legal_front_arc_instead_of_rear_stop(self):
        runtime = self.fixture.runtime
        runtime.battle_start(self.fixture.start)
        arc = math.radians(170)
        descriptor = bot_fixture._combat_descriptor(
            turret_yaw_limits=(-arc, arc), turret_speed=1.0)
        runtime._descriptors[11] = descriptor
        runtime._gun_yaw_limits[11] = (-arc, arc, True)
        state = runtime.states[11]
        state.update(x=0.0, y=0.0, z=0.0, yaw=0.0, pitch=0.0, roll=0.0,
                     turret_yaw=arc, aim_yaw=arc, gun_pitch=0.0)
        target = {'id': 2, 'kind': 'human', 'network_id': 2, 'alive': True,
                  'position': (math.sin(-arc) * 100.0, 0.0,
                               math.cos(-arc) * 100.0)}
        command = {'_ballistic_solution': {
            'aim_position': target['position'], 'yaw': -arc,
            'pitch': 0.0, 'flight_time': 1.0}}
        runtime._update_gun_aim(state, command, target, 0.1)
        self.assertLess(state['turret_yaw'], arc)
        for unused in range(80):
            runtime._update_gun_aim(state, command, target, 0.1)
            self.assertLessEqual(abs(state['turret_yaw']), arc)
        self.assertAlmostEqual(-arc, state['turret_yaw'])
        self.assertTrue(state['gun_aligned'])

    def test_limited_hull_hold_converges_without_alternating_with_driver(self):
        for minimum, maximum in ((-0.1, 0.1), (0.0, 0.0), (0.2, 0.8)):
            yaw, directions = 0.0, []
            for unused in range(500):
                turn, throttle, active = driver.combat_hull_aim(
                    yaw, 1.0, minimum, maximum,
                    -1.0, 0.0, 'arrived', True)
                directions.append(turn)
                yaw += turn * 0.04
            self.assertTrue(all(value >= 0 for value in directions))
            self.assertAlmostEqual(0.0, directions[-1], places=8)
            relative = driver._angle_delta(1.0, yaw)
            self.assertGreaterEqual(relative + 1e-8, minimum)
            self.assertLessEqual(relative - 1e-8, maximum)
        self.assertEqual((-1.0, -1.0, False), driver.combat_hull_aim(
            0.0, 1.0, -.1, .1, -1.0, -1.0, 'reverse_turn', True))

    def test_reported_su122_retreat_keeps_route_turn_over_gun_aim(self):
        # Airfield 17:52:41/46/50: SU-122-44 wanted a right route pivot,
        # but the visible target made hull aiming replace it with a left turn.
        for yaw, mode in ((-.7429965, 'withdraw'),
                          (-1.1123639, 'low_health_retreat'),
                          (-.3324339, 'low_health_retreat')):
            self.assertEqual((1.0, 0.0, False), driver.combat_hull_aim(
                yaw, yaw - 1.0, -.1, .1, 1.0, 0.0, 'drive', True,
                combat_mode=mode, movement_intent=True))
        for mode in ('route', 'advance', 'advance_contact', 'flank',
                     'take_cover', 'friendly_lane_reposition'):
            self.assertEqual((.4, 1.0, False), driver.combat_hull_aim(
                0., -1., -.1, .1, .4, 1., 'drive', True,
                combat_mode=mode, movement_intent=True))

    def test_navigation_wait_and_backout_keep_control_with_visible_target(self):
        for recovery, turn, throttle in (
                ('nav_wait', 0., 0.), ('physical_hold', 0., 0.),
                ('reverse_turn', -1., -.72)):
            self.assertEqual((turn, throttle, False), driver.combat_hull_aim(
                0., 1., -.1, .1, turn, throttle, recovery, True,
                combat_mode='engage', movement_intent=True))
        self.assertEqual((0., 0., False), driver.combat_hull_aim(
            0., 1., -.1, .1, 0., 0., 'arrived', True,
            combat_mode='route', movement_intent=True))
        self.assertEqual((1., 0., True), driver.combat_hull_aim(
            0., 1., -.1, .1, 0., 0., 'arrived', True,
            combat_mode='cover_hold', movement_intent=False))

    def test_artillery_planning_survives_continuous_gun_slew(self):
        controller = ArtilleryController()
        descriptor = bot_fixture._combat_descriptor()
        descriptor.gun.shots = ({'speed': 425.0, 'gravity': 143.0,
                                'maxDistance': 10000.0},)
        descriptor.gun.pitchLimits = {'absolute': (-0.8, 0.15)}
        source = {'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0}
        target = {'kind': 'human', 'network_id': 2,
                  'position': (0.0, 0.0, 560.0), 'speed': 0.0}
        solution = None
        for frame in range(100):
            now = frame / 24.0
            source['turret_yaw'] = frame * 0.001
            source['gun_pitch'] = -frame * 0.002
            solution = controller.solution(source, target, descriptor, 0, now)
            if solution is not None:
                break
            self.assertLessEqual(controller.advance(
                now, 1, lambda *unused: None), 1)
        self.assertIsNotNone(solution)
        self.assertGreater(frame, 1)
        moved = dict(source, x=5.0)
        self.assertEqual((False, None), controller.result(moved, target, 0, now))

    def test_artillery_pending_refresh_keeps_elevation_but_cannot_fire(self):
        runtime = self.fixture.runtime
        runtime.battle_start(self.fixture.start)
        state = runtime.states[11]
        state.update(x=0.0, y=0.0, z=0.0, yaw=0.0, pitch=0.0, roll=0.0,
                     turret_yaw=0.0, aim_yaw=0.0, gun_pitch=-0.2,
                     profile={'class_tag': 'SPG'})
        target = {'id': 2, 'kind': 'human', 'network_id': 2, 'alive': True,
                  'position': (0.0, 0.0, 500.0)}
        proved = {'yaw': 0.0, 'pitch': -0.2, 'flight_time': 1.0,
                  'aim_position': target['position']}
        runtime._update_gun_aim(
            state, {'_ballistic_solution': proved}, target, 0.1)
        self.assertTrue(state['gun_aligned'])
        pending = {'_ballistic_solution': None, 'aim_position': target['position']}
        runtime._update_gun_aim(state, pending, target, 1.0)
        self.assertAlmostEqual(-0.2, state['gun_pitch'])
        self.assertFalse(state['gun_aligned'])
        self.assertIsNone(pending['_ballistic_solution'])
        target['network_id'] = 3
        runtime._update_gun_aim(state, pending, target, 1.0)
        self.assertNotIn(11, runtime._spg_aim_solutions)

    def test_spg_full_update_reaches_a_launch_after_real_arc_queue_and_slew(self):
        descriptor = bot_fixture._combat_descriptor(
            gun_speed=0.15, turret_speed=0.25, dispersion=0.01)
        descriptor.gun.shots = ({
            'shell': {'effectsIndex': 0}, 'speed': 425.0,
            'gravity': 143.0, 'maxDistance': 10000.0},)
        descriptor.gun.pitchLimits = {'absolute': (-0.8, 0.15)}
        command = {
            'target_yaw': 0.0, 'throttle': 0.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': True,
            'target_id': self.fixture.module.HUMAN_TARGET_ID_BASE + 2,
            'fire_range': 1400.0, 'combat_mode': 'artillery_hold',
            'aim_position': (0.0, 0.0, 560.0),
            'face_position': (0.0, 0.0, 560.0),
            'move_position': (0.0, 0.0, 0.0),
            'recovery_mode': 'arrived', 'movement_intent': False,
        }
        now = [0.0]
        controller = ArtilleryController(origin_resolver=lambda source, gun:
            runtime._exact_shot_origin(source, gun, 0))

        def firing_lane(source, target):
            ready, solution = controller.request(
                source, target, descriptor, 0, now[0])
            return bool(ready and solution is not None)

        def exact_launch(source, target, gun, shell, sequence,
                         yaw, pitch, flight, stamp):
            ready, receipt = controller.request_launch(
                source, target, gun, shell, sequence,
                runtime._exact_shot_origin(source, gun, shell),
                yaw, pitch, flight, stamp)
            return receipt if ready else None

        runtime = self.fixture.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            adapter_factory=lambda *args, **kwargs:
                bot_fixture._FixedAdapter(command),
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=firing_lane,
            ballistic_solution_probe=controller.solution,
            artillery_launch_probe=exact_launch,
            artillery_launch_cancel=controller.cancel_launch,
            artillery_friendly_lane_probe=lambda *unused: {'clear': True},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=bot_fixture._spawn_resolver,
            baked_graph=bot_fixture._graph())
        runtime.battle_start(self.fixture.start)
        state = runtime.states[11]
        state.update(x=0.0, y=0.0, z=0.0, yaw=0.0, pitch=0.0, roll=0.0,
                     aim_yaw=0.0, turret_yaw=0.0, gun_pitch=0.0,
                     profile={'class_tag': 'SPG'})
        runtime._gun_states[11].elapsed = 100.0
        player = bot_fixture._admit_player({
            'id': 2, 'team': 1, 'alive': True,
            'x': 0.0, 'y': 0.0, 'z': 560.0})
        # The target is beyond the SPG's own 445 m spotting cap. Exercise
        # a real allied human observer instead of granting omniscient sight.
        observer = bot_fixture._admit_player({
            'id': 3, 'team': 2, 'alive': True,
            'x': 30.0, 'y': 0.0, 'z': 500.0})
        launches = []
        for frame in range(1, 721):
            now[0] = frame / 24.0
            self.assertLessEqual(controller.advance(
                now[0], 4, lambda *unused: None), 4)
            messages = runtime.update(
                1.0 / 24.0, now[0], players=[player, observer])
            for message in messages:
                launches.extend(message.get('launches', ()))
            if launches:
                break
        self.assertTrue(launches, 'SPG launch stalled: state=%r aim=%r intent=%r' % (
            {key: state.get(key) for key in (
                'target_id', 'gun_pitch', 'gun_aligned', 'fire_seq',
                'speed', 'clip', 'shell_index')},
            runtime._ballistic_solution_cache.get(11),
            runtime._artillery_intents.get(11)))
        self.assertEqual(1, state['fire_seq'])
        self.assertLess(state['gun_pitch'], -0.1)
        self.assertEqual(1, len(launches))
        self.assertEqual(1, launches[0]['fire_seq'])

    def test_artillery_family_progress_survives_native_pose_settling(self):
        descriptor = bot_fixture._combat_descriptor()
        descriptor.gun.shots = ({
            'shell': {'effectsIndex': 0}, 'speed': 425.0,
            'gravity': 143.0, 'maxDistance': 10000.0},)
        descriptor.gun.pitchLimits = {'absolute': (-1.55, 0.15)}
        source = {'id': 11, 'x': 0.0, 'y': 0.0, 'z': 0.0}
        target = {'kind': 'human', 'network_id': 2,
                  'position': (0.0, 0.0, 200.0)}
        controller = ArtilleryController()
        solution = None
        for frame in range(1200):
            now = frame / 24.0
            source.update(y=0.0001 * math.sin(frame),
                          pitch=0.00001 * math.cos(frame),
                          roll=0.00001 * math.sin(frame))
            def wall(first, second):
                return ((0.0, 5.0, 50.0) if first[2] <= 50.0 <= second[2]
                        and max(first[1], second[1]) < 10.0 else None)
            self.assertLessEqual(controller.advance(now, 1, wall), 1)
            solution = controller.solution(source, target, descriptor, 0, now)
            if solution is not None:
                break
        self.assertIsNotNone(solution, 'suspension settling restarted every arc')
        self.assertEqual('high', solution['arc'])
        moved = dict(source, x=0.2)
        self.assertEqual((False, None), controller.result(moved, target, 0, now))

    def test_spg_real_server_planner_and_physical_muzzle_reach_launch(self):
        descriptor = bot_fixture._combat_descriptor(
            gun_speed=0.15, turret_speed=0.25, dispersion=0.01)
        descriptor.gun.shots = ({
            'shell': {'effectsIndex': 0}, 'speed': 425.0,
            'gravity': 143.0, 'maxDistance': 10000.0},)
        descriptor.gun.pitchLimits = {'absolute': (-0.8, 0.15)}
        descriptor.activeTurretPosition = 0
        descriptor.turret['gunPosition'] = (0.0, 0.0, 1.0)
        descriptor.gun.hitTester = bot_fixture._HitTester1513(
            (-0.1, -0.1, 0.0), (0.1, 0.1, 5.0))

        def muzzle(source, gun, *unused):
            from gui.mods.offline_lan_0922 import shot_geometry
            return shot_geometry.barrel_world_point(
                gun, (source['x'], source['y'], source['z']),
                source['yaw'], source.get('pitch', 0.0), source.get('roll', 0.0),
                source.get('turret_yaw', 0.0), source.get('gun_pitch', 0.0))

        command = {
            'target_yaw': 0.0, 'throttle': 0.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': True,
            'target_id': self.fixture.module.HUMAN_TARGET_ID_BASE + 2,
            'fire_range': 1400.0, 'combat_mode': 'artillery_hold',
            'aim_position': (0.0, 0.0, 560.0),
            'face_position': (0.0, 0.0, 560.0),
            'move_position': (0.0, 0.0, 0.0),
            'recovery_mode': 'arrived', 'movement_intent': False,
        }
        now = [0.0]
        controller = ArtilleryController(origin_resolver=lambda source, gun:
            runtime._exact_shot_origin(source, gun, 0))

        def firing_lane(source, target):
            ready, solution = controller.request(
                source, target, descriptor, 0, now[0])
            return bool(ready and solution is not None)

        def exact_launch(source, target, gun, shell, sequence,
                         yaw, pitch, flight, stamp):
            ready, receipt = controller.request_launch(
                source, target, gun, shell, sequence,
                runtime._exact_shot_origin(source, gun, shell),
                yaw, pitch, flight, stamp)
            return receipt if ready else None

        runtime = self.fixture.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            direct_launch_origin_probe=muzzle,
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=firing_lane,
            ballistic_solution_probe=controller.solution,
            artillery_launch_probe=exact_launch,
            artillery_launch_cancel=controller.cancel_launch,
            artillery_friendly_lane_probe=lambda *unused: {'clear': True},
            ground_probe=lambda *unused: 0.0,
            physics_ground_probe=lambda *unused: 0.0,
            spawn_resolver=bot_fixture._spawn_resolver,
            baked_graph=bot_fixture._flat_open_graph())
        manifest = bot_fixture.bot_state_rows.bots(
            runtime.battle_start(self.fixture.start)[0])
        state = runtime.states[11]
        state.update(x=0.0, y=0.0, z=0.0, yaw=0.0, pitch=0.0, roll=0.0,
                     aim_yaw=0.0, turret_yaw=0.0, gun_pitch=0.0,
                     profile={'class_tag': 'SPG'})
        runtime._gun_states[11].elapsed = 100.0
        player = bot_fixture._admit_player({
            'id': 2, 'team': 1, 'alive': True,
            'x': 0.0, 'y': 0.0, 'z': 560.0})
        # The target is beyond the SPG's own 445 m spotting cap. Exercise
        # a real allied human observer instead of granting omniscient sight.
        observer = bot_fixture._admit_player({
            'id': 3, 'team': 2, 'alive': True,
            'x': 30.0, 'y': 0.0, 'z': 500.0})
        manifest[0]['profile'] = dict(state['profile'], class_tag='SPG',
                                     fire_range=1400.0, desired_range=650.0,
                                     dominant_role='artillery')
        # A predeployed rear anchor isolates gun control from route travel.
        manifest[0]['route'] = {'id': 'rear', 'waypoints': [
            {'x': 0.0, 'y': 0.0, 'z': 0.0, 'hold': True}]}
        planner = bot_fixture.BotPlanner()
        planner_states = [dict(state, world_pose=True)]
        launches = []
        orders_seen = []
        for frame in range(1, 721):
            now[0] = frame / 24.0
            self.assertLessEqual(controller.advance(
                now[0], 4, lambda *unused: None), 4)
            messages = runtime.update(
                1.0 / 24.0, now[0], players=[player, observer])
            for message in messages:
                launches.extend(message.get('launches', ()))
                if message.get('type') == 'bot_state':
                    planner_states = [
                        BattleState._sanitize_bot_state(row, manifest[0], None)
                        for row in bot_fixture.bot_state_rows.bots(message)]
                elif message.get('type') == 'bot_observation':
                    planner.report_contacts(message['contacts'],
                        planner.known_targets(planner_states, [player, observer]), now[0])
            orders = planner.build_orders(
                manifest, planner_states, [player, observer], now[0])
            orders_seen.extend(orders['orders'])
            runtime._apply_orders({
                'bot_orders': orders['orders'],
                'bot_order_revision': frame})
            if launches:
                break
        self.assertTrue(launches, 'SPG planner launch stalled: state=%r aim=%r intent=%r orders=%r' % (
            {key: state.get(key) for key in (
                'target_id', 'gun_pitch', 'gun_aligned', 'fire_seq',
                'speed', 'clip', 'shell_index')},
            runtime._ballistic_solution_cache.get(11),
            runtime._artillery_intents.get(11), orders_seen[-1:]))
        self.assertTrue(any(order.get('fire_allowed') for order in orders_seen))
        self.assertEqual(1, state['fire_seq'])
        self.assertLess(state['gun_pitch'], -0.1)
        self.assertEqual(1, len(launches))
        self.assertEqual(1, launches[0]['fire_seq'])

    def test_spg_high_arc_on_cross_slope_survives_planning_refresh_and_launches(self):
        descriptor = bot_fixture._combat_descriptor(
            gun_speed=0.15, turret_speed=0.25, dispersion=0.01,
            turret_yaw_limits=(-0.1, 0.1))
        descriptor.gun.shots = ({
            'shell': {'effectsIndex': 0}, 'speed': 425.0,
            'gravity': 143.0, 'maxDistance': 10000.0},)
        descriptor.gun.pitchLimits = {'absolute': (-1.55, 0.15)}
        descriptor.activeTurretPosition = 0
        descriptor.turret['gunPosition'] = (0.0, 0.0, 1.0)
        descriptor.gun.hitTester = bot_fixture._HitTester1513(
            (-0.1, -0.1, 0.0), (0.1, 0.1, 5.0))

        def muzzle(source, gun, *unused):
            from gui.mods.offline_lan_0922 import shot_geometry
            return shot_geometry.barrel_world_point(
                gun, (source['x'], source['y'], source['z']),
                source['yaw'], source.get('pitch', 0.0), source.get('roll', 0.0),
                source.get('turret_yaw', 0.0), source.get('gun_pitch', 0.0))

        command = {
            'target_yaw': 0.0, 'throttle': 0.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': True,
            'target_id': self.fixture.module.HUMAN_TARGET_ID_BASE + 2,
            'fire_range': 1400.0, 'combat_mode': 'artillery_hold',
            'aim_position': (0.0, 0.0, 200.0),
            'face_position': (0.0, 0.0, 200.0),
            'move_position': (0.0, 0.0, 0.0),
            'recovery_mode': 'arrived', 'movement_intent': False,
        }
        now = [0.0]
        controller = ArtilleryController(origin_resolver=lambda source, gun:
            runtime._exact_shot_origin(source, gun, 0))

        def firing_lane(source, target):
            ready, solution = controller.request(
                source, target, descriptor, 0, now[0])
            return bool(ready and solution is not None)

        def exact_launch(source, target, gun, shell, sequence,
                         yaw, pitch, flight, stamp):
            ready, receipt = controller.request_launch(
                source, target, gun, shell, sequence,
                runtime._exact_shot_origin(source, gun, shell),
                yaw, pitch, flight, stamp)
            return receipt if ready else None

        runtime = self.fixture.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            direct_launch_origin_probe=muzzle,
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=firing_lane,
            ballistic_solution_probe=controller.solution,
            artillery_launch_probe=exact_launch,
            artillery_launch_cancel=controller.cancel_launch,
            artillery_friendly_lane_probe=lambda *unused: {'clear': True},
            ground_probe=lambda x, z, *unused: 0.03 * x,
            physics_ground_probe=lambda x, z, *unused: 0.03 * x,
            spawn_resolver=bot_fixture._spawn_resolver,
            baked_graph=bot_fixture._flat_open_graph())
        manifest = bot_fixture.bot_state_rows.bots(
            runtime.battle_start(self.fixture.start)[0])
        state = runtime.states[11]
        state.update(x=0.0, y=0.0, z=0.0, yaw=0.0, pitch=0.0, roll=0.0,
                     aim_yaw=0.0, turret_yaw=0.0, gun_pitch=0.0,
                     profile={'class_tag': 'SPG'})
        runtime._gun_states[11].elapsed = 100.0
        player = bot_fixture._admit_player({
            'id': 2, 'team': 1, 'alive': True,
            'x': 0.0, 'y': 0.0, 'z': 200.0})
        # Keep an allied observer in the same real visibility pipeline.
        observer = bot_fixture._admit_player({
            'id': 3, 'team': 2, 'alive': True,
            'x': 30.0, 'y': 0.0, 'z': 150.0})
        manifest[0]['profile'] = dict(state['profile'], class_tag='SPG',
                                     fire_range=1400.0, desired_range=650.0,
                                     dominant_role='artillery')
        # A predeployed rear anchor isolates gun control from route travel.
        manifest[0]['route'] = {'id': 'rear', 'waypoints': [
            {'x': 0.0, 'y': 0.0, 'z': 0.0, 'hold': True}]}
        planner = bot_fixture.BotPlanner()
        planner_states = [dict(state, world_pose=True)]
        launches = []
        orders_seen = []
        def wall(first, second):
            return ((0.0, 5.0, 50.0) if first[2] <= 50.0 <= second[2]
                    and max(first[1], second[1]) < 10.0 else None)

        for frame in range(1, 1441):
            now[0] = frame / 24.0
            self.assertLessEqual(controller.advance(
                now[0], 4, wall), 4)
            messages = runtime.update(
                1.0 / 24.0, now[0], players=[player, observer])
            for message in messages:
                launches.extend(message.get('launches', ()))
                if message.get('type') == 'bot_state':
                    planner_states = [
                        BattleState._sanitize_bot_state(row, manifest[0], None)
                        for row in bot_fixture.bot_state_rows.bots(message)]
                elif message.get('type') == 'bot_observation':
                    planner.report_contacts(message['contacts'],
                        planner.known_targets(planner_states, [player, observer]), now[0])
            orders = planner.build_orders(
                manifest, planner_states, [player, observer], now[0])
            orders_seen.extend(orders['orders'])
            runtime._apply_orders({
                'bot_orders': orders['orders'],
                'bot_order_revision': frame})
            if launches:
                break
        self.assertTrue(launches, 'SPG planner launch stalled: state=%r aim=%r intent=%r orders=%r' % (
            {key: state.get(key) for key in (
                'target_id', 'gun_pitch', 'gun_aligned', 'fire_seq',
                'speed', 'clip', 'shell_index')},
            runtime._ballistic_solution_cache.get(11),
            runtime._artillery_intents.get(11), orders_seen[-1:]))
        self.assertTrue(any(order.get('fire_allowed') for order in orders_seen))
        self.assertEqual(1, state['fire_seq'])
        self.assertGreater(abs(state['yaw']), 0.01)
        self.assertLess(state['gun_pitch'], -0.1)
        self.assertEqual(1, len(launches))
        self.assertEqual(1, launches[0]['fire_seq'])


    def test_limited_td_on_a_side_slope_turns_hull_until_physical_gun_can_fire(self):
        descriptor = bot_fixture._combat_descriptor(
            turret_yaw_limits=(-0.02, 0.02), dispersion=0.01)
        descriptor.activeTurretPosition = 0
        descriptor.turret['gunPosition'] = (0.0, 0.0, 0.0)
        descriptor.gun.hitTester = bot_fixture._HitTester1513(
            (-0.1, -0.1, 0.0), (0.1, 0.1, 4.0))
        descriptor.gun.pitchLimits = {'absolute': (-0.8, 0.3)}
        self._run_td_firing_case(descriptor, False)

    def test_hydraulic_fixed_gun_enters_siege_and_reaches_a_physical_launch(self):
        travel = bot_fixture._combat_descriptor(
            turret_yaw_limits=(-0.005, 0.005), dispersion=0.01)
        siege = bot_fixture._combat_descriptor(
            turret_yaw_limits=(-0.005, 0.005), dispersion=0.01)
        for enabled, descriptor in ((False, travel), (True, siege)):
            descriptor.activeTurretPosition = 0
            descriptor.turret['gunPosition'] = (0.0, 0.0, 0.0)
            descriptor.gun.hitTester = bot_fixture._HitTester1513(
                (-0.1, -0.1, 0.0), (0.1, 0.1, 4.0))
            descriptor.type = types.SimpleNamespace(
                name='sweden:S11_Strv_103B',
                hullAimingParams={'pitch': {
                    'isAvailable': True, 'isEnabled': enabled,
                    'wheelCorrectionCenterZ': 0.0,
                    'wheelsCorrectionSpeed': 0.2,
                    'wheelsCorrectionAngles': {'pitchMin': -0.5, 'pitchMax': 0.3}}})
            descriptor.gun.pitchLimits = {'absolute': (-0.01, 0.01)}
            descriptor.gun.staticPitch = 0.0
            descriptor.gun.staticTurretYaw = 0.0
        composite = types.SimpleNamespace(
            hasSiegeMode=True, defaultVehicleDescr=travel, siegeVehicleDescr=siege)
        self._run_td_firing_case(composite, True)

    def _run_td_firing_case(self, descriptor, hydraulic):
        # An observed elevated target on a transverse ground slope. The compass
        # bearing is zero, but the physical gun's local yaw is outside its arc.
        command = {
            'target_yaw': 0.0, 'throttle': 0.0, 'turn': 0.0,
            'shell_index': 0, 'fire_allowed': True,
            'target_id': self.fixture.module.HUMAN_TARGET_ID_BASE + 2,
            'fire_range': 500.0, 'combat_mode': 'engage',
            'aim_position': (0.0, 30.0, 60.0),
            'face_position': (0.0, 30.0, 60.0),
            'move_position': (0.0, 0.0, 0.0),
            'recovery_mode': 'arrived', 'movement_intent': False,
        }
        from gui.mods.offline_lan_0922 import shot_geometry

        def muzzle(source, gun, *unused):
            return shot_geometry.barrel_world_point(
                gun, (source['x'], source['y'], source['z']),
                source['yaw'], source.get('pitch', 0.0), source.get('roll', 0.0),
                source.get('turret_yaw', 0.0), source.get('gun_pitch', 0.0))

        def ground(x, z, *unused):
            return 0.4 * x

        runtime = self.fixture.module.BotRuntime(
            1, descriptor_resolver=lambda unused: descriptor,
            player_descriptor_resolver=lambda unused: bot_fixture._combat_descriptor(),
            direct_launch_origin_probe=muzzle,
            direction_probe=lambda *unused: {'clear': True, 'slope': 0.0},
            visibility_probe=lambda *unused: True,
            firing_lane_probe=lambda *unused: True,
            friendly_lane_probe=lambda *unused: {'clear': True},
            ground_probe=ground, physics_ground_probe=ground,
            spawn_resolver=bot_fixture._spawn_resolver,
            baked_graph=bot_fixture._flat_open_graph())
        start = dict(self.fixture.start)
        if hydraulic:
            start['bots'] = [dict(bot_fixture.bot_state_rows.bots(start)[0],
                                  vehicle='sweden:S11_Strv_103B')]
        runtime.battle_start(start)
        state = runtime.states[11]
        state.update(x=0.0, y=0.0, z=0.0, yaw=0.0,
                     aim_yaw=0.0, turret_yaw=0.0, gun_pitch=0.0,
                     profile={'class_tag': 'AT-SPG'})
        runtime._gun_states[11].elapsed = 100.0
        runtime._apply_orders({
            'bot_order_revision': 1, 'bot_orders': [dict(
                command, id=11, team=2, target_kind='human', target_id=2,
                throttle_override=0.0)]})
        # Establish the actual ground attitude before acquiring a target.
        # Otherwise an early shot can leave while the hull is still flat.
        for frame in range(1, 49):
            runtime.update(1.0 / 24.0, frame / 24.0, players=[])
        initial_yaw = state['yaw']
        initial_direction = driver.barrel_direction(0.0, -math.atan2(31.0, 60.0))
        local_yaw, unused_pitch = shot_geometry.world_direction_to_local_gun_angles(
            initial_direction, state['yaw'], state['pitch'], state['roll'])
        self.assertGreater(abs(local_yaw), 0.08)
        player = bot_fixture._admit_player({
            'id': 2, 'team': 1, 'alive': True, 'x': 0.0, 'y': 30.0, 'z': 60.0})
        launches, yaws = [], []
        modes = set()
        for frame in range(1, 1441):
            messages = runtime.update(1.0 / 24.0, 2.0 + frame / 24.0, players=[player])
            yaws.append(state['yaw'])
            modes.add(state.get('siege_state'))
            for message in messages:
                launches.extend(message.get('launches', ()))
            if launches:
                break
        self.assertTrue(launches, 'TD stalled: state=%r aim=%r' % (
            {key: state.get(key) for key in (
                'yaw', 'pitch', 'roll', 'gun_pitch', 'turret_yaw', 'gun_aligned',
                'siege_state', 'speed', 'hull_aiming')},
            runtime._ballistic_solution_cache.get(11)))
        self.assertGreater(abs(state['yaw'] - initial_yaw), 1.0e-6)
        self.assertLess(max(yaws) - min(yaws), 1.0)
        if hydraulic:
            self.assertIn(self.fixture.module.siege_mechanics.SWITCHING_ON, modes)
            self.assertIn(self.fixture.module.siege_mechanics.ENABLED, modes)


class PresentedCollisionTests(unittest.TestCase):
    def test_oriented_height_culling_keeps_a_real_sloped_hull_overlap(self):
        from gui.mods.offline_lan_0922 import tank_collision as collision
        shape = (1.5, 3.5, -0.8, 2.0)
        first = {'id': 1, 'kind': 'bot', 'x': 0.0, 'y': 0.0, 'z': 0.0,
                 'yaw': 0.0, 'pitch': -0.6, 'roll': 0.0, 'shape': shape,
                 'mass': 25000.0, 'vx': 0.0, 'vz': 0.0}
        second = dict(first, id=2, y=3.0, z=2.7, pitch=0.0)
        contact_point = (0.0, 2.4, 2.5)
        self.assertTrue(collision.body_contains_point(first, contact_point))
        self.assertTrue(collision.body_contains_point(second, contact_point))
        for owner in ('single', 'pairs'):
            with self.subTest(owner=owner):
                result = (collision.resolve_tank(first, [second], dt=0.1)
                          if owner == 'single' else
                          collision.resolve_pairs([first, second], 0.1)[1])
                self.assertNotEqual((0.0, 0.0), result['correction'])
        above = dict(second, y=10.0)
        self.assertEqual((0.0, 0.0),
            collision.resolve_tank(first, [above], dt=0.1)['correction'])

    def test_height_interval_contains_all_pitched_and_rolled_corners(self):
        from gui.mods.offline_lan_0922 import tank_collision as collision
        shape = (1.6, 3.4, -0.3, 2.1)
        for pitch, roll in ((0.0, 0.0), (-0.6, 0.3), (0.4, -0.5), (0.0, 2.0)):
            axes = collision.pose_axes(0.8, pitch, roll)
            corners = [5.0 + sum(point[i] * axes[i][1] for i in range(3))
                       for point in ((x, y, z) for x in (-shape[0], shape[0])
                                     for y in shape[2:]
                                     for z in (-shape[1], shape[1]))]
            low, high = collision.vertical_interval(5.0, shape, pitch, roll)
            self.assertAlmostEqual(min(corners), low)
            self.assertAlmostEqual(max(corners), high)

    def test_remote_player_contacts_follow_presented_pose_without_changing_wire_state(self):
        import test_port_0922_battle_runtime as fixture
        from gui.mods.offline_lan_0922 import tank_collision as collision
        runtime = fixture._runtime()
        battle = fixture.BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        descriptor = fixture._Descriptor()
        runtime.bigworld.entities[12] = fixture._Vehicle(
            12, descriptor, fixture._Vector(), (0, 0, 0), {'health': 500})
        state = {'id': 2, 'x': 0.0, 'y': 0.0, 'z': 100.0, 'yaw': 0.0,
                 'speed': 5.0, 'team': 2, 'alive': True,
                 'effective_params': fixture._effective_params_snapshot()}
        record = {'engine_id': 12, 'network_id': 2, 'kind': 'player',
                  'ready': True, 'state': state}
        battle._records['player:2'] = record
        pose = {'x': 0.0, 'y': 0.0, 'z': 1.0, 'yaw': 0.4,
                'pitch': 0.1, 'roll': 0.2}
        with mock.patch.object(battle, '_apply_record_pose'):
            battle._update_entity({'entity': 'player:2', 'kind': 'player',
                'interpolated': True, 'pose': pose, 'presentation_time_us': 100})
        bodies = battle._contact_tanks(
            (0.0, 0.0, 0.0), collision.chassis_shape(descriptor), 0.1)
        self.assertEqual(1, len(bodies))
        self.assertEqual((1.0, 0.4, 0.1, 0.2),
            tuple(bodies[0][key] for key in ('z', 'yaw', 'pitch', 'roll')))
        self.assertEqual(100.0, state['z'])

    def test_local_candidate_filter_keeps_contact_on_a_pitched_nose(self):
        import test_port_0922_battle_runtime as fixture
        from gui.mods.offline_lan_0922 import tank_collision as collision
        runtime = fixture._runtime()
        battle = fixture.BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        battle._local_pitch = -0.6
        shape = (1.5, 3.5, -0.8, 2.0)
        runtime.bigworld.entities[11] = fixture._Vehicle(
            11, fixture._Descriptor(), fixture._Vector(), (0, 0, 0), {'health': 500})
        battle._records['bot:11'] = {
            'engine_id': 11, 'network_id': 11, 'kind': 'bot', 'ready': True,
            'state': {'id': 11, 'x': 0.0, 'y': 3.0, 'z': 2.7,
                      'yaw': 0.0, 'speed': 0.0, 'team': 2,
                      'collision_shape': shape}}
        bodies = battle._contact_tanks((0.0, 0.0, 0.0), shape)
        self.assertEqual([11], [body['network_id'] for body in bodies])

class ArtilleryTargetLeaseTests(unittest.TestCase):
    def test_acquired_spg_target_survives_proof_gap_but_cannot_fire_without_lane(self):
        import test_port_0922_server_bot_ai as fixture
        planner = fixture.BotPlanner()
        route = fixture._route('rear', [(0, 0, True)])
        manifest = [fixture._bot(11, 1, 0, route)]
        states = [fixture._state(11, 1, 0, 0)]
        players = [{'id': 2, 'team': 2, 'alive': True},
                   {'id': 3, 'team': 2, 'alive': True}]
        known = planner.known_targets(states, players)

        def orders(now, contacts):
            planner.report_contacts(contacts, known, now)
            return planner.build_orders(manifest, states, players, now)['orders'][0]

        blocked = fixture._contact(2, 0, 200, [])
        first = orders(1.0, [blocked])
        self.assertIsNone(first['target_id'])
        acquired = orders(2.0, [fixture._contact(2, 0, 200, [11])])
        self.assertEqual(2, acquired['target_id'])
        self.assertTrue(acquired['fire_allowed'])
        pending = orders(12.0, [blocked])
        self.assertEqual(2, pending['target_id'])
        self.assertFalse(pending['fire_allowed'])
        alternative = orders(13.0, [blocked, fixture._contact(3, 0, 210, [11])])
        self.assertEqual(3, alternative['target_id'])
        self.assertTrue(alternative['fire_allowed'])
        hidden = orders(14.0, [dict(blocked, visible=False),
            dict(fixture._contact(3, 0, 210, []), visible=False)])
        self.assertIsNone(hidden['target_id'])
        self.assertFalse(hidden['fire_allowed'])
