"""Report 115952: solid motion across live, wreck and native world owners."""
import math
import unittest
from unittest import mock

from test_port_0922_tank_collision import tank_collision as contact, _tank
import test_port_0922_bot_runtime as bot_tests


class TranslationSweepTests(unittest.TestCase):
    def test_clear_first_sweep_is_reused_without_another_query(self):
        moving = _tank(1, 0., 0.)
        movement = (0., -2.)
        others = [_tank(2, 0., 8.)]
        with mock.patch.object(contact, 'translation_fraction',
                               wraps=contact.translation_fraction) as sweep:
            expected = contact.slide_translation(moving, movement, others)
            self.assertEqual(1, sweep.call_count)
            sweep.reset_mock()
            fraction = contact.translation_fraction(moving, movement, others)
            actual = contact.slide_translation(
                moving, movement, others, first_fraction=fraction)
            self.assertEqual(1, sweep.call_count)
        self.assertEqual(1., fraction)
        self.assertEqual(movement, expected)
        self.assertEqual(expected, actual)

    def test_zero_first_fraction_still_sweeps_the_projected_tangent(self):
        for second_obstacle in (False, True):
            with self.subTest(second_obstacle=second_obstacle):
                moving = _tank(1, 0., 0.)
                movement = (.2, 1.)
                others = [_tank(2, 2.99, 0.)]
                if second_obstacle:
                    others.append(_tank(3, 0., 7.2))
                with mock.patch.object(contact, 'translation_fraction',
                                       wraps=contact.translation_fraction) as sweep, \
                        mock.patch.object(contact, '_slide_fraction',
                                          wraps=contact._slide_fraction) as tangent:
                    expected = contact.slide_translation(moving, movement, others)
                    self.assertEqual(1, sweep.call_count)
                    self.assertEqual(1, tangent.call_count)
                    sweep.reset_mock()
                    tangent.reset_mock()
                    fraction = contact.translation_fraction(moving, movement, others)
                    self.assertEqual(0., fraction)
                    sweep.reset_mock()
                    actual = contact.slide_translation(
                        moving, movement, others, first_fraction=fraction)
                    self.assertEqual(0, sweep.call_count)
                    self.assertEqual(1, tangent.call_count)
                    self.assertEqual((0., 1.), tangent.call_args.args[1])
                self.assertEqual(expected, actual)
                self.assertEqual(0., actual[0])
                self.assertAlmostEqual(.21 if second_obstacle else 1., actual[1])

    def test_slide_geometry_expires_before_the_next_live_roster(self):
        moving = _tank(1, 0., 0.)
        side = _tank(2, 2.99, 0.)
        ahead = _tank(3, 0., 7.2)
        movement = (.2, 1.)
        for ahead_z, ahead_y, expected in ((7.2, 0., .21),
                                          (30., 0., 1.),
                                          (7.2, 20., 1.),
                                          (7.2, 0., .21)):
            with self.subTest(ahead_z=ahead_z, ahead_y=ahead_y):
                ahead.update(z=ahead_z, y=ahead_y)
                accepted = contact.slide_translation(
                    moving, movement, [side, ahead])
                self.assertEqual(0., accepted[0])
                self.assertAlmostEqual(expected, accepted[1])

    def test_slide_far_peers_do_not_hide_a_later_tangent_blocker(self):
        moving = _tank(1, 0., 0.)
        side = _tank(2, 3., 0.)
        ahead = _tank(3, 0., 12.)
        movement = (8., 8.)
        far = [_tank(i, 100. + i, -100.) for i in range(4, 31)]
        for others in ([side] + far + [ahead], [ahead] + far + [side]):
            fraction = contact.translation_fraction(moving, movement, others)
            accepted = contact.slide_translation(
                moving, movement, others, first_fraction=fraction)
            self.assertAlmostEqual(.01, accepted[0])
            self.assertAlmostEqual(5.01, accepted[1])

    def test_visible_drive_and_residual_push_stop_at_the_actual_remote_hull(self):
        import test_port_0922_battle_runtime as t
        for alive in (True, False):
            for hz in (25,60,144):
                runtime = t._runtime()
                battle = t.BattleRuntime(runtime)
                battle.client = t._Client()
                battle._avatar = runtime.bigworld.avatar
                battle._local_physics = dict(t._effective_params_snapshot()['physics'],mass=100575.)
                local = t._Vehicle(10,t._Descriptor(),t._Vector(),(0,0,0),{'health':500})
                peer = _tank(1000011,0.,8.,mass=31370.)
                peer.update(network_id=11,kind='bot',alive=alive)
                battle._collision_shape = lambda unused: contact.DEFAULT_SHAPE
                battle._contact_tanks = lambda *args, **kw: [peer]
                battle._motion_is_clear = lambda *args, **kw: True
                battle._baked_pose_safe = lambda *args: True
                battle._poll_local_ram_contact_episodes = lambda *args: None
                position = (0.,0.,0.)
                with mock.patch('sys.stdout'):
                    for unused in range(hz):
                        battle._local_speed = 10.
                        battle._local_contact_start_position = position
                        position = battle._resolve_local_tank_contacts(
                            local,(0.,0.,position[2]+10./hz),0.,1./hz)
                        self.assertLessEqual(position[2],1.0101)
                for row in battle._local_contact_pushes.values():
                    self.assertEqual([0.,0.],row[4:6])

    def test_a_clear_endpoint_cannot_skip_an_intervening_hull(self):
        for yaw in (0., .7, math.pi/2, -2.1):
            for distance in (10., 100.):
                with self.subTest(yaw=yaw, distance=distance):
                    moving = _tank(1, 0., 0.)
                    blocker = _tank(2, 0., 8., yaw=yaw)
                    fraction = contact.translation_fraction(moving, (0., distance), [blocker])
                    self.assertGreater(fraction, 0.)
                    self.assertLess(fraction, 1.)
                    hit = contact.obb_contact(0., distance*fraction, 0., moving['shape'],
                                              0., 8., yaw, blocker['shape'])
                    self.assertAlmostEqual(contact.POSITION_SLOP, hit[2], places=8)

    def test_overlap_can_escape_or_slide_but_cannot_deepen(self):
        a, b = _tank(1, 0., 0.), _tank(2, 0., 6.)
        self.assertEqual(1., contact.translation_fraction(a, (0., -100.), [b]))
        self.assertEqual(1., contact.translation_fraction(a, (10., 0.), [b]))
        self.assertEqual(0., contact.translation_fraction(a, (0., 100.), [b]))
        b['y'] = 20.
        self.assertEqual(1., contact.translation_fraction(a, (0., 100.), [b]))

    def test_position_ownership_cannot_change_the_mass_weighted_impulse(self):
        for first_mass, second_mass in ((100575., 31370.), (31370., 100575.),
                                       (35500., 21100.), (21100., 35500.)):
            a = _tank(1, 0., 0., mass=first_mass, vz=10.)
            b = _tank(2, 0., 6.9, mass=second_mass)
            free = contact.resolve_pairs([a,b], .1)
            b['position_fixed'] = True
            fixed = contact.resolve_pairs([a,b], .1)
            for actor in (1,2):
                self.assertEqual(free[actor]['delta_velocity'], fixed[actor]['delta_velocity'])
            self.assertEqual(free[1]['correction'], fixed[1]['correction'])
            self.assertEqual((0.,0.), fixed[2]['correction'])
            a['position_fixed'], b['position_fixed'] = True, False
            other_owner = contact.resolve_pairs([a,b], .1)
            self.assertEqual(free[2]['correction'], other_owner[2]['correction'])
            self.assertEqual((0.,0.), other_owner[1]['correction'])
            self.assertAlmostEqual(0., first_mass*fixed[1]['correction'][1] +
                                   second_mass*other_owner[2]['correction'][1])
            total = first_mass*fixed[1]['delta_velocity'][1] + second_mass*fixed[2]['delta_velocity'][1]
            self.assertAlmostEqual(0., total, places=6)


class WorkerSolidMotionTests(unittest.TestCase):
    setUp = bot_tests.ShovedWreckTests.setUp
    tearDown = bot_tests.ShovedWreckTests.tearDown
    _runtime = bot_tests.ShovedWreckTests._runtime

    def prepare(self):
        runtime = self._runtime()
        for i, z in ((11,0.), (12,8.)):
            runtime.states[i].update(x=0.,y=0.,z=z,yaw=0.,speed=0.,
                pitch=0.,roll=0.,push_x=0.,push_z=0.,collision_shape=contact.DEFAULT_SHAPE)
        runtime._clear = lambda *args: True
        return runtime

    def test_drive_sweep_reuse_is_limited_to_each_movement_and_roster(self):
        runtime = self.prepare()
        state, peer = runtime.states[11], runtime.states[12]
        collision = self.module.tank_collision
        for peer_z, distance, expected in ((8., 20., 1.01),
                                           (8., -2., -2.),
                                           (30., 20., 20.)):
            with self.subTest(peer_z=peer_z, distance=distance):
                state['z'], peer['z'] = distance, peer_z
                with mock.patch.object(collision, 'translation_fraction',
                                       wraps=collision.translation_fraction) as sweep:
                    runtime._guard_tank_translations(
                        [], {11: (0., 0., 0.), 12: (0., 0., peer_z)})
                    self.assertEqual(2, sweep.call_count)
                self.assertAlmostEqual(expected, state['z'])
                self.assertEqual(expected != distance, '_contact_drive_sweep' in state)
                self.assertEqual(peer_z, peer['z'])

    def test_contact_sweep_reuse_is_limited_to_each_movement_and_roster(self):
        runtime = self.prepare()
        state, peer = runtime.states[11], runtime.states[12]
        collision = self.module.tank_collision
        for peer_z, distance, expected in ((8., 20., 1.01),
                                           (8., -2., -2.),
                                           (30., 20., 20.)):
            with self.subTest(peer_z=peer_z, distance=distance):
                state['z'], peer['z'] = 0., peer_z
                with mock.patch.object(collision, 'translation_fraction',
                                       wraps=collision.translation_fraction) as sweep:
                    runtime._apply_tank_contact_response(state,
                        {'delta_velocity': (0., 0.), 'correction': (0., distance)},
                        .1, advance_push=False, apply_friction=False)
                    self.assertEqual(1, sweep.call_count)
                self.assertAlmostEqual(expected, state['z'])
                self.assertEqual(peer_z, peer['z'])

    def test_repeated_wreck_shoves_reconcile_velocity_and_ack_at_original_mass(self):
        import copy
        import types
        import test_port_0922_battle_runtime as t
        travel = {}
        for wreck_mass in (23496.0, 100575.0):
            worker = self.prepare()
            worker.states.pop(12)
            wreck = worker.states[11]
            wreck.update(alive=False, health=0, mass=wreck_mass, grounded_once=True,
                         contact_push_acks=[])
            worker._physics_params_for(11)['mass'] = wreck_mass
            worker._player_collision_profile = lambda raw: dict(
                mass=100575., shape=contact.DEFAULT_SHAPE, ram_profile={},
                physics=worker._physics_params_for(11))
            native = t._runtime()
            battle = t.BattleRuntime(native)
            battle.client = t._Client()
            battle._avatar = native.bigworld.avatar
            battle._bots = types.SimpleNamespace(states={},
                replica_contact_params=lambda raw, descriptor: dict(
                    t.battle_runtime_module.vehicle_physics.derive_params(descriptor),
                    mass=wreck_mass))
            battle._local_physics = dict(t._effective_params_snapshot()['physics'], mass=100575.)
            local = t._Vehicle(10, t._Descriptor(), t._Vector(), (0,0,0), {'health':500})
            remote = t._Vehicle(11, t._Descriptor(), t._Vector(), (0,0,0), {'health':0})
            native.bigworld.entities[11] = remote
            initial = copy.deepcopy(wreck)
            battle._records = {'bot:11': dict(engine_id=11, network_id=11,
                kind='bot', local=False, ready=True, tombstone=False,
                state=initial, presented_pose=dict(initial))}
            battle._collision_shape = lambda unused: contact.DEFAULT_SHAPE
            battle._motion_is_clear = lambda *args, **kw: True
            battle._baked_pose_safe = lambda *args: True
            battle._materialize_record = mock.Mock()
            battle._fallback_postmortem_viewpoint = mock.Mock()
            battle._apply_record_pose = mock.Mock()
            sync = t.battle_runtime_module.SnapshotSync(battle.client.player_id)
            sync.manifest({'round_id':5, 'bots':[initial]})
            def receive(event):
                if event['type'] == 'destroy':
                    battle._destroy_entity(event)
                elif event['type'] == 'update':
                    battle._update_entity(event)
            sync.on_event = receive
            sync.snapshot({'round_id':5, 'server_tick':1, 'bots':[initial]})
            player = dict(id=battle.client.player_id, alive=True, x=100., y=0.,
                          z=100., yaw=0., speed=0., team=1, tank_pushes=[])
            travelled = []
            with mock.patch('sys.stdout'):
                for cycle in range(3):
                    record = battle._records['bot:11']
                    position = (record['presented_pose']['x'], 0.,
                                record['presented_pose']['z']-6.99)
                    bodies = battle._contact_tanks(position, contact.DEFAULT_SHAPE)
                    self.assertEqual(wreck_mass, bodies[0]['mass'])
                    self.assertFalse(bodies[0]['immovable'])
                    self.assertEqual((0.,0.), bodies[0]['physical_velocity'])
                    battle._local_speed = 6.
                    battle._local_push_x = battle._local_push_z = 0.
                    battle._resolve_local_tank_contacts(local, position, 0., .1)
                    row = list(battle._local_contact_pushes[11])
                    player['tank_pushes'] = [row]
                    before = wreck['z']
                    worker._resolve_tank_contacts([player], cycle*10.+1., .1)
                    self.assertEqual([[player['id']]+row[1:]], wreck['contact_push_acks'])
                    # Let native track resistance settle the wreck. Repeated
                    # delivery of the same checkpoint must not add momentum.
                    for tick in range(100):
                        worker._resolve_tank_contacts([player], cycle*10.+1.1+tick*.1, .1)
                    self.assertEqual((0.,0.), (wreck['push_x'],wreck['push_z']))
                    travelled.append(wreck['z']-before)
                    sync.snapshot({'round_id':5, 'server_tick':cycle+2,
                                   'bots':[copy.deepcopy(wreck)]})
                    state = record['state']
                    self.assertEqual(wreck['contact_push_acks'], state['contact_push_acks'])
                    self.assertEqual((0.,0.), (state['push_x'],state['push_z']))
                    self.assertFalse(state['alive'])
                    self.assertEqual({}, battle._local_ram_receipts)
            self.assertTrue(all(distance > 0. for distance in travelled), travelled)
            for distance in travelled[1:]:
                self.assertAlmostEqual(travelled[0], distance, places=5)
            travel[wreck_mass] = travelled[0]
        self.assertGreater(travel[23496.], travel[100575.])

    def test_a_shoved_wreck_cannot_cross_a_third_vehicle(self):
        runtime = self.prepare()
        state = runtime.states[11]
        state['alive'] = False
        runtime._bleed_contact_push = lambda state,x,z,dt: (x,z)
        runtime._apply_tank_contact_response(state,
            {'delta_velocity': (0.,100.), 'correction': (0.,20.)}, .1)
        self.assertAlmostEqual(1.01, state['z'], places=8)
        self.assertLess(state['z'], runtime.states[12]['z'])

    def test_native_hull_sweep_vetoes_a_nudge_missed_by_planning_rays(self):
        for step in (0., .1):
            runtime = self.prepare()
            runtime.states.pop(12)
            state = runtime.states[11]
            state['movement_dir'] = 1
            seen = []
            def wall(actor, position, yaw, speed, descriptor, dt, now,
                     commit_enabled, motion_yaw=None):
                seen.append((actor, speed*dt, descriptor, commit_enabled, motion_yaw))
                self.assertEqual(0, state['movement_dir'])
                return 'hard'
            runtime.motion_resolver = wall
            runtime._apply_tank_contact_response(state,
                {'delta_velocity': (0.,0.), 'correction': (.2,.3)}, step)
            self.assertEqual((0.,0.), (state['x'],state['z']))
            self.assertEqual(1, state['movement_dir'])
            self.assertEqual(1, len(seen))
            self.assertAlmostEqual(math.hypot(.2,.3), seen[0][1])
            self.assertIs(runtime._descriptors[11], seen[0][2])
            self.assertFalse(seen[0][3])
            self.assertAlmostEqual(math.atan2(.2,.3), seen[0][4])

    def test_drive_sweep_keeps_a_live_player_solid_with_receipt_transport(self):
        runtime = self.prepare()
        runtime.states.pop(12)
        state = runtime.states[11]
        runtime._player_collision_profile = lambda raw: dict(
            mass=100575.,shape=contact.DEFAULT_SHAPE,ram_profile={},
            physics=runtime._physics_params_for(11))
        player = dict(id=1,x=0.,y=0.,z=8.,yaw=0.,alive=True,speed=0.,
                      team=1,tank_pushes=[])
        state['z'] = 20.
        state['speed'] = 10.
        runtime._guard_tank_translations([player], {11:(0.,0.,0.)})
        self.assertAlmostEqual(1.01,state['z'])
        runtime._resolve_tank_contacts([player],1.,.1)
        self.assertLessEqual(state['z'],1.011)

    def test_contact_recovery_cannot_ignore_a_live_player(self):
        runtime = self.prepare()
        runtime.states.pop(12)
        runtime._player_collision_profile = lambda raw: dict(
            mass=100575.,shape=contact.DEFAULT_SHAPE,ram_profile={},
            physics=runtime._physics_params_for(11))
        player = dict(id=1,x=0.,y=0.,z=-6.,yaw=0.,alive=True,speed=0.,
                      team=1,tank_pushes=[])
        runtime._resolve_tank_contacts([player],1.,.1)
        state = runtime.states[11]
        expected = .99 * 100575. / (state['mass'] + 100575.)
        self.assertAlmostEqual(expected, state['z'], places=4)
        self.assertEqual(-6., player['z'])
        # An unchanged peer still occupies its real pose. Repeated recovery
        # converges without pretending the remote owner already moved it.
        for tick in range(1, 10):
            runtime._resolve_tank_contacts([player],1.+tick*.1,.1)
        self.assertAlmostEqual(.99, state['z'], places=8)

    def test_opposing_drive_endpoints_cannot_exchange_sides(self):
        runtime = self.prepare()
        for state in runtime.states.values():
            state['speed'] = 100.
        runtime.states[11]['z'], runtime.states[12]['z'] = 20., -12.
        runtime._guard_tank_translations([], {11:(0.,0.,0.),12:(0.,0.,8.)})
        self.assertLess(runtime.states[11]['z'],runtime.states[12]['z'])
        runtime._resolve_tank_contacts([],1.,.1)
        self.assertLess(runtime.states[11]['z'],runtime.states[12]['z'])

    def test_wreck_and_bot_stay_out_of_each_other_and_the_world_under_repeated_push(self):
        runtime = self.prepare()
        wreck, bot = runtime.states[11], runtime.states[12]
        wreck['alive'] = False
        wall_z = bot['z']
        def wall(actor, position, yaw, speed, descriptor, dt, now,
                 commit_enabled, motion_yaw=None):
            end = position[2] + math.cos(motion_yaw)*abs(speed)*dt
            return 'hard' if actor == 12 and end > wall_z+1.e-9 else 'clear'
        runtime.motion_resolver = wall
        with mock.patch('sys.stdout'):
            for index in range(100):
                runtime._apply_tank_contact_response(wreck,
                    {'delta_velocity': (0.,2.), 'correction': (0.,0.)}, 0.,
                    advance_push=False, apply_correction=False)
                runtime._resolve_tank_contacts([], index*.1, .1)
                self.assertLessEqual(bot['z'],wall_z+1.e-8)
                hit = contact.obb_contact(wreck['x'],wreck['z'],0.,wreck['collision_shape'],
                                          bot['x'],bot['z'],0.,bot['collision_shape'])
                self.assertTrue(hit is None or hit[2] <= contact.POSITION_SLOP+1.e-6)
