import math
import types
import unittest
from unittest import mock
from test_port_0922_battle_runtime import BattleRuntime, _runtime, _Vehicle, _Vector
from test_port_0922_turret_obstacles import descriptor, row
from gui.mods.offline_lan_0922 import rigid_turret


class TurretRuntimeContactTests(unittest.TestCase):
    def test_visual_arc_never_enters_worker_solver_or_obstacle_registry(self):
        runtime, battle = self.setup_body()
        battle._worker_mode = True
        battle._detached_turret_rows['bot:17'] = row()
        battle._turret_server_time_ms = lambda unused: 10000
        battle._detached_turret_obstacles = mock.Mock()
        battle._advance_turret_support = mock.Mock()
        battle._advance_detached_turrets(10.)
        battle._advance_turret_support.assert_not_called()
        battle._detached_turret_obstacles.add.assert_not_called()
        self.assertEqual({}, battle._turret_bodies)

    def setup_body(self):
        runtime = _runtime()
        battle = BattleRuntime(runtime)
        battle._avatar = runtime.bigworld.avatar
        td = descriptor()
        td.turret.weight, td.gun.weight = 4000., 1000.
        td.physics = {'weight': 50000.}
        for key, engine_id in (('bot:17', 117), ('player:1', 101)):
            runtime.bigworld.entities[engine_id] = _Vehicle(engine_id, td, _Vector(), (0,0,0), {'health': 500})
            battle._records[key] = dict(engine_id=engine_id, ready=True, kind=key.split(':')[0], network_id=int(key.split(':')[1]))
        battle.client = types.SimpleNamespace(player_id=1)
        battle._bots = types.SimpleNamespace(
            states={17: dict(id=17, x=100., y=0., z=0., yaw=0., speed=0., alive=False)},
            _native_motion_for=lambda unused: None)
        battle._collide_rigid_turret = lambda a,b: None
        battle._apply_turret_bot_response = lambda *args: None
        return runtime, battle

    def test_worker_consumes_one_momentum_checkpoint_even_after_player_leaves(self):
        runtime, battle = self.setup_body()
        accepted = row()
        accepted['flight']['origin'] = (0., 10., 0.)
        accepted['flight']['segments'][0]['origin'] = (0., 10., 0.)
        accepted['flight']['velocity'] = (0.,0.,0.)
        accepted['flight']['segments'][0]['velocity'] = (0.,0.,0.)
        battle._detached_turret_rows['bot:17'] = accepted
        player = dict(id=1, x=-100., y=0., z=0., yaw=0., speed=0.,
                      turret_pushes=[['bot:17', 3, 5000., 0., 0., 0., 0., 0.]])
        battle._authority_players = lambda: [player]
        battle._advance_turret_support(1040)
        body = battle._turret_bodies['bot:17']
        self.assertAlmostEqual(1., body.velocity[0])
        self.assertEqual([['player:1', 3, 5000., 0., 0., 0., 0., 0.]], body.acks)
        battle._advance_turret_support(1080)
        self.assertAlmostEqual(1., body.velocity[0])
        self.assertGreater(body.position[0], .07)
        proposal = battle._detached_turret_proposals['bot:17']
        self.assertEqual(2, proposal['motion_seq'])
        self.assertEqual(body.acks, proposal['flight']['body']['acks'])

    def test_visible_side_contact_publishes_reciprocal_momentum_and_can_reverse(self):
        runtime, battle = self.setup_body()
        td = runtime.bigworld.entities[117].typeDescriptor
        body = rigid_turret.Body(rigid_turret.geometry.turret_components(td),
                                dict(position=(0,1,0), attitude=(0,0,0), grounded=True))
        battle._detached_turret_rows['bot:17'] = rigid_turret.revision(row(), body, 1000)
        battle._local_physics = {'mass': 50000.}
        battle._local_speed = 5.
        battle._motion_is_clear = lambda *args, **kwargs: True
        player = runtime.bigworld.entities[101]
        position = (-1.4,1.,0.)
        result = battle._resolve_local_turret_contacts(player, position, math.pi/2, .04)
        sent = battle._local_turret_pushes['bot:17'][:]
        self.assertGreater(sent[2], 0)
        self.assertEqual(0., sent[3])
        self.assertLess(battle._local_speed, 5.)
        self.assertEqual(position[1], result[1])
        battle._local_speed = -5.
        battle._resolve_local_turret_contacts(player, result, math.pi/2, .04)
        self.assertEqual(sent, battle._local_turret_pushes['bot:17'])
        self.assertEqual(-5., battle._local_speed)

    def test_native_query_failure_rolls_back_pose_and_receipt_cursor_together(self):
        runtime, battle = self.setup_body()
        accepted = row()
        battle._detached_turret_rows['bot:17'] = accepted
        player = dict(id=1, x=-100., y=0., z=0., yaw=0., speed=0.,
                      turret_pushes=[['bot:17', 1, 5000., 0., 0., 0., 0., 0.]])
        battle._authority_players = lambda: [player]
        def fail(a, b):
            raise RuntimeError('scenery query failed')
        battle._collide_rigid_turret = fail
        battle._advance_turret_support(1040)
        self.assertEqual([], battle._turret_bodies['bot:17'].acks)
        self.assertEqual(0., battle._turret_bodies['bot:17'].velocity[0])
        self.assertNotIn('bot:17', battle._turret_sim_times)
        battle._collide_rigid_turret = lambda a,b: None
        with mock.patch('gui.mods.offline_lan_0922.battle_runtime._PROFILE_CLOCK', return_value=0.):
            battle._advance_turret_support(1080)
        self.assertAlmostEqual(1., battle._turret_bodies['bot:17'].velocity[0])
        self.assertAlmostEqual(.08, battle._turret_bodies['bot:17'].position[0])
        self.assertEqual(1080, battle._turret_sim_times['bot:17'])
        battle._advance_turret_support(1080)
        self.assertAlmostEqual(.08, battle._turret_bodies['bot:17'].position[0])

    def test_landed_visible_contact_moves_the_canonical_body_once(self):
        runtime, battle = self.setup_body()
        td = runtime.bigworld.entities[117].typeDescriptor
        body = rigid_turret.Body(rigid_turret.geometry.turret_components(td),
            dict(position=(0., 1., 0.), attitude=(0., 0., 0.), grounded=True))
        battle._detached_turret_rows['bot:17'] = rigid_turret.revision(row(), body, 1000)
        self.assertTrue(battle._detached_turret_rows['bot:17']['flight']['landed'])
        battle._local_physics = {'mass': 50000.}
        battle._local_speed = 5.
        battle._motion_is_clear = lambda *args, **kwargs: True
        battle._resolve_local_turret_contacts(runtime.bigworld.entities[101],
                                              (-1.4, 1., 0.), math.pi/2, .04)
        checkpoint = battle._local_turret_pushes['bot:17'][:]
        self.assertGreater(checkpoint[2], 0.)
        player = dict(id=1, x=-1.4, y=1., z=0., yaw=math.pi/2,
                      speed=battle._local_speed, alive=True, turret_pushes=[checkpoint])
        battle._authority_players = lambda: [player]
        with mock.patch('gui.mods.offline_lan_0922.battle_runtime._PROFILE_CLOCK', return_value=0.):
            battle._advance_turret_support(1040)
            moved = battle._turret_bodies['bot:17']
            self.assertGreater(moved.position[0], 0.)
            self.assertEqual([['player:1']+checkpoint[1:]], moved.acks)
            first = moved.frame()
            battle._advance_turret_support(1040)
            self.assertEqual(first, moved.frame())

    def test_late_callback_preserves_debt_without_starving_other_bodies(self):
        runtime, battle = self.setup_body()
        accepted = row()
        accepted['flight']['origin'] = (0., 10., 0.)
        accepted['flight']['segments'][0]['origin'] = (0., 10., 0.)
        battle._detached_turret_rows['bot:17'] = accepted
        battle._detached_turret_rows['bot:18'] = dict(accepted, actor_id=18)
        battle._records['bot:18'] = dict(battle._records['bot:17'], engine_id=118, network_id=18)
        runtime.bigworld.entities[118] = _Vehicle(
            118, runtime.bigworld.entities[117].typeDescriptor, _Vector(), (0, 0, 0), {'health': 0})
        battle._authority_players = lambda: []
        calls = []
        battle._collide_rigid_turret = lambda a, b: calls.append((a, b))
        timer = [0.]
        def measured_cost():
            timer[0] += .0011
            return timer[0]
        with mock.patch('gui.mods.offline_lan_0922.battle_runtime._PROFILE_CLOCK', side_effect=measured_cost):
            battle._advance_turret_support(6000)
            self.assertAlmostEqual(1020, battle._turret_sim_times['bot:17'])
            self.assertAlmostEqual(1020, battle._turret_sim_times['bot:18'])
            self.assertLessEqual(len(calls), 2*16*2)
        # Once the work is cheap, both clocks consume the retained interval
        # without another 125 callbacks of artificially slowed flight.
        with mock.patch('gui.mods.offline_lan_0922.battle_runtime._PROFILE_CLOCK', return_value=0.):
            battle._advance_turret_support(6000)
        self.assertEqual(6000, battle._turret_sim_times['bot:17'])
        self.assertEqual(6000, battle._detached_turret_proposals['bot:17']['motion_time_ms'])
        self.assertEqual(6000, battle._turret_sim_times['bot:18'])

    def test_ten_hz_worker_advances_the_whole_interval_when_queries_fit_budget(self):
        runtime, battle = self.setup_body()
        battle._detached_turret_rows['bot:17'] = row()
        battle._authority_players = lambda: []
        with mock.patch('gui.mods.offline_lan_0922.battle_runtime._PROFILE_CLOCK', return_value=0.):
            for server_ms in range(1100, 1601, 100):
                battle._advance_turret_support(server_ms)
                self.assertEqual(server_ms, battle._turret_sim_times['bot:17'])
        self.assertAlmostEqual(1.-9.81*.6, battle._turret_bodies['bot:17'].velocity[1])

    def test_scenery_sweep_reuses_live_filter_and_checks_outside_its_bounds(self):
        runtime, battle = self.setup_body()
        td = runtime.bigworld.entities[117].typeDescriptor
        body = rigid_turret.Body(rigid_turret.geometry.turret_components(td),
                                dict(position=(400, 8, 200), attitude=(0, 0, 0)))
        live_filter = object()
        battle._prepared_ground_filter = mock.Mock(return_value=live_filter)
        battle._collide_down = mock.Mock(return_value=(_Vector(400, 3, 200), _Vector(0, 1, 0)))
        fallback = battle._collide_rigid_turret = mock.Mock(return_value=None)
        query = battle._rigid_turret_scenery_query(body, .04)
        for unused in range(64):
            self.assertEqual(((400., 3., 200.), (0., 1., 0.)), query((400, 8, 200), (400, 2, 200)))
        self.assertEqual(1, battle._prepared_ground_filter.call_count)
        self.assertIs(live_filter, battle._collide_down.call_args.args[2])
        query((800, 8, 200), (800, 2, 200))
        fallback.assert_called_once()
