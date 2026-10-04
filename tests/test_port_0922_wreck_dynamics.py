"""Report 143607: off-centre wreck impulses, cliff release and powered shoves."""
import copy
import math
import types
import unittest
from unittest import mock

import test_port_0922_bot_runtime as bt
import test_port_0922_battle_runtime as vt
from test_port_0922_tank_collision import tank_collision as c, _tank
from test_port_0922_tank_contact_ledger import ledger, bot_state_codec


class AngularContactTests(unittest.TestCase):
    def test_visible_contact_resweeps_drive_and_impulse_as_one_vector(self):
        native=vt._runtime();battle=vt.BattleRuntime(native)
        battle.client=vt._Client();battle._avatar=native.bigworld.avatar
        local=vt._Vehicle(10,vt._Descriptor(),vt._Vector(),(0,0,0),{'health':500})
        shape=c.DEFAULT_SHAPE
        peer=_tank(2,(shape[0]+shape[1])/math.sqrt(2.)+shape[0]-.01,0.)
        peer.update(network_id=2)
        battle._collision_shape=lambda unused:shape
        battle._contact_tanks=lambda *args,**kwargs:[peer]
        battle._motion_is_clear=lambda *args,**kwargs:True
        battle._baked_pose_safe=lambda *args:True
        battle._poll_local_ram_contact_episodes=lambda *args:None
        battle._local_speed=2.
        travel=.2/math.sqrt(2.)
        with mock.patch('sys.stdout'):
            accepted=battle._resolve_local_tank_contacts(
                local,(travel,0.,travel),math.pi/4.,.1,(0.,0.,0.))
        self.assertAlmostEqual(0.,accepted[0])
        tangent=(battle._local_speed*math.cos(math.pi/4.)+battle._local_push_z)*.1
        self.assertAlmostEqual(tangent,accepted[2])
        self.assertGreater(accepted[2],travel*.5)

    def test_combined_wreck_world_sweep_covers_every_translated_yaw_slice(self):
        native=vt._runtime();battle=vt.BattleRuntime(native)
        battle._avatar=native.bigworld.avatar
        battle._destructibles=types.SimpleNamespace(
            native_replacement_bsp_active=lambda:False,
            _vehicle_body_bbox=lambda unused:((-1.7,-.4,-3.5),(1.7,1.8,3.2),None))
        for blocked in (False,True):
            with mock.patch.object(vt.battle_runtime_module.world_collision,
                    'check_horizontal_collision',
                    side_effect=['clear','hard' if blocked else 'clear']) as probe:
                self.assertEqual(not blocked,battle._native_world_rotation_is_clear(
                    (2.,3.,4.),0.,.16,vt._Descriptor(),include_static=True,
                    translation=(.2,.4),record_local=False))
            self.assertEqual(2,probe.call_count)
            for index,call in enumerate(probe.call_args_list):
                self.assertAlmostEqual(2.+index*.1,call.args[3].x)
                self.assertAlmostEqual(4.+index*.2,call.args[3].z)
                self.assertAlmostEqual(math.hypot(.1,.2),call.args[5])
                self.assertEqual(1.,call.args[8])
                self.assertAlmostEqual(math.atan2(.1,.2),call.kwargs['motion_yaw'])
                self.assertFalse(call.kwargs['exact_footprint'])
                self.assertFalse(call.kwargs['commit_enabled'])

    def test_oblique_contact_retains_tangent_without_crossing_a_second_hull(self):
        moving = _tank(1,0.,0.)
        wall = _tank(2,2.99,0.)
        movement = (.2,1.)
        self.assertEqual(0., c.translation_fraction(moving,movement,[wall]))
        self.assertEqual((0.,1.), c.slide_translation(moving,movement,[wall]))
        corner = _tank(3,0.,7.2)
        accepted = c.slide_translation(moving,movement,[wall,corner])
        self.assertAlmostEqual(0.,accepted[0])
        self.assertLess(accepted[1],.22)
        for f in range(101):
            at = dict(moving,x=accepted[0]*f/100.,z=accepted[1]*f/100.)
            for peer in (wall,corner):
                overlap = c._obb_overlap(at['x'],at['z'],at['yaw'],at['shape'],
                    peer['x'],peer['z'],peer['yaw'],peer['shape'])[2]
                self.assertLessEqual(overlap,c.POSITION_SLOP+1.e-8)

    def test_wreck_corner_sweep_admits_coupled_turn_not_translation_into_pusher(self):
        wreck = _tank(1,0.,0.,mass=25000.)
        pusher = _tank(2,1.5316034433,5.8740801617,yaw=2.4772432636,mass=100575.)
        move = (.2255501756,-.2342856616)
        turn = -.06348706
        self.assertEqual(0.,c.translation_fraction(wreck,move,[pusher]))
        self.assertEqual(1.,c.rotation_fraction((0,0,0),0.,turn,wreck['shape'],
                                              [pusher],translation=move))
        self.assertLess(c.rotation_fraction((0,0,0),0.,-turn,wreck['shape'],
                                           [pusher],translation=move),.01)

    def test_wreck_rotation_checks_static_world_before_any_structure_breaks(self):
        native=vt._runtime();battle=vt.BattleRuntime(native)
        battle._avatar=native.bigworld.avatar
        battle._bots=types.SimpleNamespace(states={11:dict(alive=False,pitch=0.,roll=0.)})
        battle._destructibles=types.SimpleNamespace(
            native_replacement_bsp_active=lambda:False,
            _vehicle_body_bbox=lambda unused:((-1.7,-.4,-3.5),(1.7,1.8,3.2),None))
        battle._destructible_pose_sweep=mock.Mock(return_value=dict(status='clear',requires_commit=False))
        with mock.patch.object(vt.battle_runtime_module.world_collision,
                               'check_horizontal_collision',return_value='hard') as probe:
            self.assertFalse(battle._resolve_bot_rotation(11,(0.,0.,0.),0.,.08,
                             vt._Descriptor(),.1,1.,0.))
        self.assertTrue(probe.called)
        self.assertEqual('world',battle._bot_motion_kinds[11])

    def test_a_powered_corner_delivers_wreck_torque_at_its_real_lever(self):
        a = _tank(1, 0., 0., mass=100575.)
        b = _tank(2, 2.99, 1., mass=23496.)
        a.update(traverse_speed=1., traverse_torque=500000.)
        b['alive'] = False
        solved = c.resolve_pairs([a,b], .1)
        c.traverse_impulses(c.post_contact_velocity_bodies([a,b],solved),
                            .1, angular_results=solved)
        self.assertNotEqual(0.,solved[2]['delta_yaw'])

    def test_worker_state_uses_its_mounted_shape_for_the_same_inertia(self):
        state = dict(alive=False,mass=32000.,collision_shape=(1.3,2.9,0.,2.))
        body = dict(alive=False,mass=32000.,shape=state['collision_shape'])
        self.assertEqual(c.wreck_yaw_inertia(body),c.wreck_yaw_inertia(state))

    def test_contact_lever_rotates_in_both_directions_without_adding_energy(self):
        for offset in (-2.5, 0.0, 2.5):
            a = _tank(1, offset, -6.99, mass=100575., vz=8.)
            b = _tank(2, 0., 0., mass=23496.)
            b['alive'] = False
            inertia = c.wreck_yaw_inertia(b)
            before = .5*a['mass']*a['vz']**2
            result = c.resolve_pairs([a,b], .02)
            self.assertAlmostEqual(0., sum(body['mass']*result[body['id']]['delta_velocity'][1]
                                          for body in (a,b)), places=6)
            after = 0.
            for body in (a,b):
                dv = result[body['id']]['delta_velocity']
                after += .5*body['mass']*((body['vx']+dv[0])**2+(body['vz']+dv[1])**2)
            omega = result[2]['delta_yaw']
            after += .5*inertia*omega**2
            self.assertLessEqual(after, before+1.e-6)
            if offset:
                self.assertLess(omega*offset, 0.)
            else:
                self.assertAlmostEqual(0., omega)

    def test_angular_checkpoint_is_coalesced_retried_and_acknowledged_once(self):
        sent = {}
        ledger.record(sent, 11, (1.,2.), angular=12000.)
        first = list(sent[11])
        ledger.record(sent, 11, (3.,4.), angular=-4000.)
        latest = list(sent[11])
        self.assertEqual(8000., ledger.unseen_angular(latest, None))
        self.assertEqual(-4000., ledger.unseen_angular(latest, first))
        self.assertEqual(0., ledger.unseen_angular(first, latest))
        state = dict(id=11, push_yaw=.125, contact_push_acks=[[1]+latest[1:]])
        decoded = bot_state_codec.decode_row(bot_state_codec.encode_row(state), {})
        self.assertEqual(.125, decoded['push_yaw'])
        self.assertEqual(0., ledger.pending_angular(sent, 11, decoded['contact_push_acks'], 1))
        for bad in (float('nan'),float('inf'),True):
            with self.assertRaises((ValueError,TypeError)):
                ledger.normalize([latest[:6]+[bad]])


class WreckOwnerTests(unittest.TestCase):
    setUp = bt.ShovedWreckTests.setUp
    tearDown = bt.ShovedWreckTests.tearDown
    _runtime = bt.ShovedWreckTests._runtime
    _wreck = bt.ShovedWreckTests._wreck

    def test_report_bank_releases_live_and_dead_hulls_without_drive_input(self):
        # 203327 M41: the native bank normal is an upward 30.5-degree face.
        # Player suspension has side-slip; the worker previously only changed
        # height/attitude and could perch here indefinitely after a shove.
        nx, ny, nz = -.30123034, .861268997, -.40923822
        gx, gz = -nx/ny, -nz/ny
        for alive in (True, False):
            worker,state,unused=bt.BotRuntimeTests._suspension_case(
                self, lambda x,z: gx*x+gz*z if x > -3. else -30.)
            state.update(alive=alive,speed=0.,movement_dir=0,yaw=-1.244333,
                         collision_shape=c.DEFAULT_SHAPE,mass=23496.,
                         _contact_dynamics=True)
            state['_suspension_ground_plane']=dict(gradient_x=gx,gradient_z=gz)
            worker.states={11:state}
            worker.motion_resolver=mock.Mock(return_value='clear')
            self.assertTrue(worker._wreck_is_active(state))
            with mock.patch('sys.stdout'):
                for tick in range(180):
                    before=(state['x'],state['y'],state['z'])
                    self.assertFalse(worker._update_vertical_motion(state,.05,before,state['yaw']))
            self.assertLess(state['x'],-3.)
            self.assertLess(state['y'],-10.)
            self.assertFalse(worker._suspension_param_failures)
            self.assertTrue(worker.motion_resolver.called)

    def test_passive_slide_cannot_cross_world_wall_or_other_vehicle(self):
        for obstacle in ('wall','vehicle'):
            worker=self._runtime();worker.states.pop(12)
            state=self._wreck(worker)
            state.update(x=0.,y=0.,z=0.,yaw=0.,slide_speed=3.,
                         _suspension_ground_plane=dict(gradient_x=1.,gradient_z=0.))
            worker.motion_resolver=mock.Mock(return_value='hard' if obstacle=='wall' else 'clear')
            worker._contact_motion_bodies=lambda unused: (
                [_tank(2,-2.*state['collision_shape'][0]+.01,0.)] if obstacle=='vehicle' else [])
            worker._apply_suspension_slope_slide(state,.1)
            self.assertEqual((0.,0.),(state['x'],state['z']))
            self.assertEqual(0.,state['slide_speed'])

    def test_slide_carries_through_air_but_zero_time_projection_cannot_move_it_twice(self):
        worker=self._runtime();worker.states.pop(12)
        state=self._wreck(worker)
        state.update(airborne=True,air_lateral_x=2.,air_lateral_z=-1.)
        worker.motion_resolver=mock.Mock(return_value='clear')
        before=(state['x'],state['z'])
        worker._apply_suspension_slope_slide(state,.1)
        self.assertAlmostEqual(before[0]+.2,state['x'])
        self.assertAlmostEqual(before[1]-.1,state['z'])
        after=(state['x'],state['z'])
        worker._apply_suspension_slope_slide(state,0.)
        self.assertEqual(after,(state['x'],state['z']))

    def test_live_slide_survives_the_later_combined_drive_contact_resweep(self):
        worker=self._runtime();worker.states.pop(12)
        state=worker.states[11]
        state.update(alive=True,x=0.,y=0.,z=.1,yaw=0.,speed=1.,
                     airborne=True,air_lateral_x=2.,air_lateral_z=0.,
                     _contact_drive_sweep=((0.,0.,0.),(0.,.1)))
        worker.motion_resolver=mock.Mock(return_value='clear')
        worker._apply_suspension_slope_slide(state,.1)
        self.assertEqual((.2,.1),state['_contact_drive_sweep'][1])
        worker._apply_tank_contact_response(state,dict(
            delta_velocity=(0.,0.),correction=(0.,0.)),.1)
        self.assertAlmostEqual(.2,state['x'])
        self.assertAlmostEqual(.1,state['z'])

    def test_driver_hazard_guard_does_not_reclassify_passive_slide_as_drive(self):
        worker=self._runtime();worker.states.pop(12)
        state=worker.states[11]
        state.update(x=-2.,y=-1.,z=0.,yaw=0.)
        safe=lambda graph,point,**kwargs: point[0]>=0.
        with mock.patch.object(self.module.prebaked_navigation,'pose_is_safe',side_effect=safe):
            self.assertFalse(worker._guard_realised_pose(state,(1.,0.,0.),True,0.,
                                                        navigation_pose=(.1,0.,0.)))
            self.assertEqual(-2.,state['x'])
            self.assertTrue(worker._guard_realised_pose(state,(1.,0.,0.),True,0.,
                                                       navigation_pose=(-.1,0.,0.)))
            self.assertEqual(1.,state['x'])

    def test_authority_update_advances_bank_slide_for_live_and_dead_states(self):
        gx,gz=.30123034/.861268997,.40923822/.861268997
        for alive in (True,False):
            support,initial,unused=bt.BotRuntimeTests._suspension_case(
                self,lambda x,z: gx*x+gz*z if x>-3. else -30.)
            worker=self.module.BotRuntime(1,
                descriptor_resolver=lambda unused:support._descriptors[11],
                adapter_factory=lambda *args,**kwargs:bt._FixedAdapter(
                    dict(throttle=0.,turn=0.,fire_allowed=False)),
                direction_probe=lambda *args,**kwargs:dict(
                    clear=True,collision=False,water=False,slope=0.),
                ground_probe=support._physics_ground_probe,
                physics_ground_probe=support._physics_ground_probe,
                suspension_ground_probe=support._suspension_ground_probe,
                spawn_resolver=bt._spawn_resolver,baked_graph=bt._flat_open_graph())
            worker.battle_start(self.start);worker.states.pop(12)
            state=worker.states[11]
            state.update(initial)
            state.update(alive=alive,health=500 if alive else 0,speed=0.,movement_dir=0,
                         yaw=-1.244333,_contact_dynamics=True)
            state['_suspension_ground_plane']=dict(gradient_x=gx,gradient_z=gz)
            worker.motion_resolver=lambda *args,**kwargs:'clear'
            # A driver must avoid x<0; accepted passive motion must survive
            # the same final authority guard that normally enforces it.
            with mock.patch.object(self.module.prebaked_navigation,'pose_is_safe',
                    side_effect=lambda graph,point,**kw:point[0]>=0.), \
                    mock.patch('sys.stdout'):
                for tick in range(180):worker._update_once(.05,(tick+1)*.05,[])
            self.assertLess(state['x'],-3.,repr({k:state.get(k) for k in (
                'alive','airborne','x','y','z','slide_speed','_suspension_ground_plane',
                'movement_dir','_motion_stall_pending')}))
            self.assertLess(state['y'],-10.)
            self.assertFalse(worker._suspension_param_failures)

    def test_worker_shove_crosses_finite_deck_edge_with_real_world_sweeps(self):
        # Combine the authority lifecycle with actual translation sweeps, not
        # an always-clear motion resolver. Both deck/side faces and an optional
        # independent wall return finite segment intersections.
        world = vt.battle_runtime_module.world_collision
        for alive in (True, False):
            for wall in (False, True):
                descriptor = bt._suspension_descriptor()
                def terrain(x, z):
                    return 5. if x < 2. else -15.
                def support(x, z, low, high, flat=None):
                    y = terrain(x, z)
                    return y if low <= y <= high else None
                def collide(space, start, end, mask, *unused):
                    delta = end-start
                    hits = []
                    if abs(delta.y) > 1.e-9:
                        for height in (5., -15.):
                            fraction = (height-start.y)/delta.y
                            if 0. <= fraction <= 1.:
                                point = start+delta.scale(fraction)
                                if height < 0. or point.x < 2.:
                                    hits.append((fraction, point, vt._Vector(0,1,0)))
                    if abs(delta.x) > 1.e-9:
                        fraction = (2.-start.x)/delta.x
                        if 0. <= fraction <= 1.:
                            point = start+delta.scale(fraction)
                            if 4.5 <= point.y <= 5.:
                                hits.append((fraction, point, vt._Vector(1,0,0)))
                        if wall:
                            fraction = (1.9-start.x)/delta.x
                            if 0. <= fraction <= 1.:
                                hits.append((fraction,start+delta.scale(fraction),vt._Vector(-1,0,0)))
                    if hits:
                        unused, point, normal = min(hits,key=lambda row:row[0])
                        return point, normal, 108
                    return None
                native = types.SimpleNamespace(wg_collideSegment=collide)
                worker = self.module.BotRuntime(1,
                    descriptor_resolver=lambda unused:descriptor,
                    adapter_factory=lambda *args,**kwargs:bt._FixedAdapter(
                        dict(throttle=0.,turn=0.,fire_allowed=False)),
                    direction_probe=lambda *args,**kwargs:dict(
                        clear=False,collision=False,water=True,slope=0.),
                    ground_probe=lambda x,z,hint:terrain(x,z),
                    physics_ground_probe=lambda x,z,hint:terrain(x,z),
                    suspension_ground_probe=support,
                    spawn_resolver=bt._spawn_resolver,baked_graph=bt._flat_open_graph())
                worker.battle_start(self.start);worker.states.pop(12)
                state=worker.states[11]
                state.update(x=0.,y=5.,z=0.,yaw=0.,speed=0.,alive=alive,
                    health=500 if alive else 0,grounded_once=True,_contact_dynamics=True)
                def resolve(bot_id, position, yaw, speed, desc, dt, now,
                            commit_enabled=True, motion_yaw=None):
                    return 'hard' if world.check_horizontal_collision(native,
                        types.SimpleNamespace(Vector3=vt._Vector),1,vt._Vector(position),
                        yaw,speed,desc,state.get('airborne',False),dt,
                        motion_yaw=motion_yaw,pitch=state.get('terrain_pitch',0.),
                        roll=state.get('roll',0.)) else 'clear'
                worker.motion_resolver=resolve
                receipts={}
                player=dict(id=1,team=1,x=-50.,y=5.,z=0.,yaw=0.,speed=0.,alive=True,
                    effective_params=bt._effective_params_snapshot(mass=100575.))
                with mock.patch.object(world,'prepare_horizontal_collision_filter',return_value=None), \
                        mock.patch.object(world,'_destroy_and_recast',return_value=False), \
                        mock.patch.object(self.module.prebaked_navigation,'pose_is_safe',
                            side_effect=lambda graph,point,**kw:point[0]<2.), \
                        mock.patch('sys.stdout'):
                    for tick in range(180):
                        ledger.record(receipts,11,(state['mass']*.5,0.))
                        player['tank_pushes']=list(receipts.values())
                        worker._update_once(.05,(tick+1)*.05,[player])
                with self.subTest(alive=alive,wall=wall):
                    if wall:
                        self.assertLess(state['x'],2.)
                        self.assertGreater(state['y'],4.)
                    else:
                        self.assertGreater(state['x'],4.)
                        self.assertLess(state['y'],0.)

    def test_real_replica_lifecycle_has_no_worker_cache_but_uses_mounted_mass(self):
        replica=self._runtime()
        replica.battle_start(dict(self.start,round_id=6,bot_authority_id=-1))
        self.assertFalse(replica.is_authority())
        self.assertEqual({},replica.states)
        self.assertEqual({},replica._descriptors)
        native=vt._runtime();battle=vt.BattleRuntime(native)
        battle.client=vt._Client();battle._avatar=native.bigworld.avatar
        battle._bots=replica
        battle._clock=lambda:1.
        battle._estimated_motion_time_us=lambda unused:1000000
        descriptor=vt._Descriptor()
        descriptor.physics.update(weight=35500.,enginePower=600.*735.49875)
        native.bigworld.entities[11]=vt._Vehicle(11,descriptor,vt._Vector(),(0,0,0),{'health':500})
        for alive in (True,False):
            state=dict(id=11,x=0.,y=0.,z=3.,yaw=0.,alive=alive,airborne=True,
                       speed=0.,team=2,slot=0,skill_rating=5000,
                       collision_shape=c.DEFAULT_SHAPE,vehicle='ussr:R11_MS-1')
            record=dict(engine_id=11,network_id=11,kind='bot',ready=True,
                        state=state,presented_pose=state)
            battle._records={'bot:11':record}
            battle._remember_ram_bot_snapshot(dict(bot_state_revision=1,
                bot_state_time_us=1000000,bots=[state]))
            battle._local_contact_impulses={11:[(1,1000000,71000.,-35500.,0.)]}
            with mock.patch('sys.stdout'), mock.patch.object(replica,'_physics_params_for',
                    side_effect=AssertionError('replica has no authority descriptors')):
                peer=battle._contact_tanks((0.,0.,0.),c.DEFAULT_SHAPE,.02)[0]
            self.assertEqual(35500.,peer['mass'])
            self.assertEqual((2.,-1.),peer['physical_velocity'])
            self.assertEqual(600.*735.49875,record['_contact_parameters'][1]['powerW'])
            self.assertEqual({},replica._physics_params)

    def test_worker_commits_coupled_wreck_turn_only_after_whole_world_sweep(self):
        for blocked in (False,True):
            worker=self._runtime();worker.states.pop(12)
            state=self._wreck(worker)
            state.update(x=0.,y=0.,z=0.,yaw=0.,collision_shape=(1.5,3.5,-.8,2.))
            worker._contact_motion_bodies=lambda unused: [
                _tank(2,1.5316034433,5.8740801617,yaw=2.4772432636,mass=100575.)]
            worker.motion_resolver=mock.Mock(return_value='clear')
            worker._wreck_rotation_probe=mock.Mock(return_value=not blocked)
            move=(.2255501756,-.2342856616)
            self.assertEqual(not blocked,worker._try_wreck_swept_pose(state,move,-.6348706,.1))
            self.assertEqual(move,worker._wreck_rotation_probe.call_args.kwargs['translation'])
            if blocked:
                self.assertEqual((0.,0.,0.),(state['x'],state['z'],state['yaw']))
            else:
                self.assertEqual(move,(state['x'],state['z']))
                self.assertAlmostEqual(-.06348706,state['yaw'])

    def test_wire_only_bot_state_uses_installed_mass_for_pending_momentum(self):
        worker=self._runtime();worker.states.pop(12)
        params=worker._physics_params_for(11)
        params['mass']=35500.
        native=vt._runtime();battle=vt.BattleRuntime(native)
        battle.client=vt._Client();battle._bots=worker
        battle._clock=lambda:1.
        battle._estimated_motion_time_us=lambda unused:1000000
        for alive in (True,False):
            raw=dict(worker.states[11],alive=alive,airborne=True,
                     speed=0.,yaw=0.,push_x=0.,push_z=0.,push_yaw=0.)
            published=bot_state_codec.decode_row(bot_state_codec.encode_row(raw),{})
            self.assertNotIn('mass',published)
            battle._ram_bot_history_index={11:[(1000000,1)]}
            battle._ram_bot_history={1:{11:published}}
            battle._local_contact_impulses={11:[(1,1000000,71000.,-35500.,0.)]}
            self.assertEqual((2.,-1.,0.),battle._predict_bot_contact_velocity(
                11,published,c.DEFAULT_SHAPE,params))

    def test_receipt_transit_is_measured_once_and_stops_without_pending_impulses(self):
        worker=self._runtime()
        native=vt._runtime();battle=vt.BattleRuntime(native)
        battle.client=vt._Client();battle._bots=worker
        battle._clock=lambda:1.
        battle._estimated_motion_time_us=lambda unused:1000000
        state=dict(worker.states[11],contact_push_acks=[[battle.client.player_id,1]])
        params=worker._physics_params_for(11)
        battle._ram_bot_history_index={11:[(800000,1)]}
        battle._ram_bot_history={1:{11:state}}
        battle._local_contact_impulses={11:[(1,700000,1.,0.,0.),(2,750000,2.,0.,0.)]}
        with mock.patch.object(self.module.vehicle_physics,'predict_contact_velocity',
                               return_value=(0.,0.,0.)) as predict:
            battle._predict_bot_contact_velocity(11,state,c.DEFAULT_SHAPE,params)
            self.assertAlmostEqual(.7,predict.call_args.args[3])
            battle._ram_bot_history_index={11:[(900000,1)]}
            battle._predict_bot_contact_velocity(11,state,c.DEFAULT_SHAPE,params)
            self.assertAlmostEqual(.8,predict.call_args.args[3])
            state['contact_push_acks']=[[battle.client.player_id,2]]
            battle._predict_bot_contact_velocity(11,state,c.DEFAULT_SHAPE,params)
            self.assertAlmostEqual(.9,predict.call_args.args[3])

    def test_sliding_track_friction_does_not_apply_a_second_full_yaw_brake(self):
        p=self._runtime()._physics_params_for(11)
        physics=self.module.vehicle_physics
        shape=c.DEFAULT_SHAPE
        inertia=(shape[0]**2+shape[1]**2)/3.
        for sign in (-1.,1.):
            vx,vz,omega=physics.wreck_contact_step(p,0.,5.,sign*.4,0.,shape,.1)
            self.assertAlmostEqual(0.,vx)
            self.assertGreater(sign*omega,0.)
            self.assertLess(vx*vx+vz*vz+inertia*omega*omega,25.+inertia*.16)
        self.assertEqual((0.,0.,0.),physics.wreck_contact_step(p,0.,0.,.4,0.,shape,.1))

    def test_passive_shove_uses_world_collision_without_navigation_hazard_veto(self):
        worker=self._runtime();worker.states.pop(12)
        state=self._wreck(worker)
        worker.direction_probe=mock.Mock(return_value=dict(clear=False,collision=False,water=True,slope=1.5))
        worker.motion_resolver=mock.Mock(return_value='clear')
        self.assertTrue(worker._apply_wreck_contact_response(
            state,dict(delta_velocity=(0.,4.),correction=(0.,0.)),.1))
        worker.direction_probe.assert_not_called()
        before=state['z']
        worker.motion_resolver.return_value='hard'
        worker._apply_wreck_contact_response(state,dict(delta_velocity=(0.,4.),correction=(0.,0.)),.1)
        self.assertEqual(before,state['z'])

    def test_production_wreck_only_ground_probe_activates_after_death(self):
        worker,state,unused=bt.BotRuntimeTests._suspension_case(self,lambda x,z:0.)
        worker.states={state['id']:state}
        worker._wreck_ground_probe=worker._suspension_ground_probe
        worker._suspension_ground_probe=None
        worker._suspension_params.clear()
        self.assertIsNone(worker._suspension_params_for(state['id']))
        state.update(alive=False,health=0,collision_shape=c.DEFAULT_SHAPE,mass=25000.)
        self.assertIsNotNone(worker._suspension_params_for(state['id']))
        worker._apply_wreck_contact_response(state,dict(delta_velocity=(0.,0.),correction=(0.,0.)),.02)
        self.assertFalse(worker._suspension_param_failures)

    def test_contact_promotes_live_bot_to_track_support_before_cliff_departure(self):
        worker,state,unused=bt.BotRuntimeTests._suspension_case(
            self,lambda x,z:0. if x<=0. else -30.)
        worker.states={state['id']:state}
        worker._wreck_ground_probe=worker._suspension_ground_probe
        worker._suspension_ground_probe=None
        worker._suspension_params.clear()
        state.update(x=-4.,alive=True,mass=25000.,collision_shape=c.DEFAULT_SHAPE)
        self.assertIsNone(worker._suspension_params_for(state['id']))
        worker.motion_resolver=lambda *args,**kwargs:'clear'
        with mock.patch('sys.stdout'):
            worker._apply_tank_contact_response(state,dict(
                delta_velocity=(2.,0.),correction=(0.,0.)),0.,advance_push=False)
            self.assertIsNotNone(worker._suspension_params_for(state['id']))
            # The contact moved its centre off the lip; no centre-height snap
            # may replace the now unsupported track/rigid-body state.
            state.update(x=4.,speed=0.)
            for unused in range(20):
                worker._update_vertical_motion(state,.05)
        self.assertTrue(state['airborne'])
        self.assertLess(state['y'],-1.)
        self.assertGreater(state['y'],-20.)
        self.assertFalse(worker._suspension_param_failures)

    def test_delayed_offset_push_turns_the_actual_worker_wreck(self):
        worker=self._runtime();worker.states.pop(12)
        state=self._wreck(worker)
        state.update(mass=23496.,collision_shape=c.DEFAULT_SHAPE)
        params=worker._physics_params_for(11);params['mass']=state['mass']
        human_params=dict(params,mass=100575.,powerW=1200.*735.49875)
        worker._player_collision_profile=lambda raw:dict(mass=100575.,shape=c.DEFAULT_SHAPE,
                                                        ram_profile={},physics=human_params)
        native=vt._runtime();battle=vt.BattleRuntime(native)
        battle.client=vt._Client();battle._avatar=native.bigworld.avatar
        battle._local_physics=human_params
        battle._bots=types.SimpleNamespace(states={11:copy.deepcopy(state)},
            replica_contact_params=lambda raw,descriptor:params)
        local=vt._Vehicle(10,vt._Descriptor(),vt._Vector(),(0,0,0),{'health':500})
        native.bigworld.entities[11]=vt._Vehicle(11,vt._Descriptor(),vt._Vector(),(0,0,0),{'health':0})
        record=dict(engine_id=11,network_id=11,kind='bot',local=False,ready=True,
                    tombstone=False,state=copy.deepcopy(state),presented_pose=copy.deepcopy(state))
        battle._records={'bot:11':record}
        battle._collision_shape=lambda unused:c.DEFAULT_SHAPE
        battle._motion_is_clear=lambda *args,**kwargs:True
        battle._baked_pose_safe=lambda *args:True
        battle._poll_local_ram_contact_episodes=lambda *args:None
        clock=[0.];battle._clock=lambda:clock[0]
        battle._estimated_motion_time_us=lambda unused:int(clock[0]*1000000)
        battle._remember_ram_bot_snapshot(dict(bot_state_revision=0,bot_state_time_us=0,bots=[copy.deepcopy(state)]))
        position=(-4.99,0.,3.0);queue=[];human_queue=[];published_human=None
        with mock.patch('sys.stdout'):
            for tick in range(360):
                clock[0]=(tick+1)/60.
                battle._local_speed=self.module.vehicle_physics.longitudinal_step(
                    human_params,battle._local_speed,1,False,0.,1./60.)
                start=position
                position=battle._resolve_local_tank_contacts(local,
                    (position[0]+battle._local_speed/60.,position[1],position[2]),math.pi/2.,1./60.,start)
                if tick%6==5:
                    raw=dict(id=battle.client.player_id,team=1,x=position[0],y=position[1],z=position[2],
                             yaw=math.pi/2.,speed=battle._local_speed,forward=1,
                             tank_pushes=copy.deepcopy(list(battle._local_contact_pushes.values())))
                    human_queue.append((clock[0]+.15,raw))
                    while human_queue and human_queue[0][0]<=clock[0]+1.e-9:
                        unused,published_human=human_queue.pop(0)
                    if published_human is None:
                        published_human=dict(raw,x=-4.99,speed=0.,tank_pushes=[])
                    worker._resolve_tank_contacts([published_human],clock[0],.1)
                    wire=bot_state_codec.decode_row(bot_state_codec.encode_row(state),{})
                    queue.append((clock[0]+.15,clock[0],tick+1,wire))
                while queue and queue[0][0]<=clock[0]+1.e-9:
                    unused,stamp,revision,published=queue.pop(0)
                    record.update(state=published,presented_pose=published)
                    battle._bots.states[11]=published
                    battle._remember_ram_bot_snapshot(dict(bot_state_revision=revision,
                        bot_state_time_us=int(stamp*1000000),bots=[published]))
        self.assertGreater(state['x'],1.)
        self.assertGreater(abs(state['yaw']),.1)

    def test_falling_turned_wreck_survives_the_wire(self):
        state=dict(id=11,alive=False,pitch=1.08,roll=-1.661,airborne=True,
                   push_x=2.,push_z=-3.,push_yaw=.4,reload_duration=5.,reload_time=0.)
        decoded=bot_state_codec.decode_row(bot_state_codec.encode_row(state),{})
        self.assertTrue(decoded['airborne'])
        self.assertAlmostEqual(1.08,decoded['pitch'])
        self.assertAlmostEqual(-1.661,decoded['roll'])
        from test_port_0922_server_capture import BattleState
        identity=dict(id=11,team=1,slot=0,name='Wreck',vehicle='ussr:R54_KV-5',max_health=1780)
        for trusted in (False,True):
            admitted=BattleState._sanitize_bot_state(decoded,identity,None,trusted=trusted)
            self.assertTrue(admitted['airborne'])
            self.assertAlmostEqual(1.08,admitted['pitch'])
            self.assertAlmostEqual(-1.661,admitted['roll'])
        p=self._runtime()._physics_params_for(11)
        self.assertEqual((2.,-3.,.4),self.module.vehicle_physics.predict_contact_velocity(
            p,decoded,c.DEFAULT_SHAPE,0.,.3,[]))

    def test_visible_offcentre_collision_reaches_worker_as_the_frozen_yaw_impulse(self):
        worker=self._runtime();worker.states.pop(12)
        state=self._wreck(worker)
        state['collision_shape']=c.DEFAULT_SHAPE
        native=vt._runtime();battle=vt.BattleRuntime(native)
        battle.client=vt._Client();battle._avatar=native.bigworld.avatar
        battle._local_physics=dict(vt._effective_params_snapshot()['physics'],mass=100575.)
        local=vt._Vehicle(10,vt._Descriptor(),vt._Vector(),(0,0,0),{'health':500})
        peer=_tank(1000011,0.,0.,mass=state['mass'])
        peer.update(alive=False,kind='bot',network_id=11,physical_velocity=(0.,0.))
        battle._collision_shape=lambda unused:c.DEFAULT_SHAPE
        battle._contact_tanks=lambda *args,**kw:[peer]
        battle._motion_is_clear=lambda *args,**kw:True
        battle._baked_pose_safe=lambda *args:True
        battle._poll_local_ram_contact_episodes=lambda *args:None
        battle._local_speed=8.
        with mock.patch('sys.stdout'):
            battle._resolve_local_tank_contacts(local,(2.5,0.,-6.99),0.,.02)
            row=battle._local_contact_pushes[11]
            self.assertLess(row[6],0.)
            raw=dict(id=battle.client.player_id,tank_pushes=[row])
            # A later pose change must not rebind the accepted impulse lever.
            state['yaw']=.5
            worker._consume_human_contact_pushes([raw],1.)
            self.assertAlmostEqual(row[6]/c.wreck_yaw_inertia(state),state['push_yaw'])
            before=state['push_yaw']
            worker._consume_human_contact_pushes([raw],2.)
            self.assertEqual(before,state['push_yaw'])

    def test_repeated_offset_impulses_turn_wreck_but_world_veto_still_wins(self):
        for blocked in (False,True):
            worker = self._runtime()
            worker.states.pop(12)
            state = self._wreck(worker)
            worker._wreck_rotation_probe = mock.Mock(return_value=not blocked)
            worker._player_collision_profile = lambda raw: dict(mass=100575.,
                shape=c.DEFAULT_SHAPE,ram_profile={},physics=worker._physics_params_for(11))
            sent = {}
            inertia = c.wreck_yaw_inertia(state)
            with mock.patch('sys.stdout'):
                for i in range(2):
                    before = state['yaw']
                    ledger.record(sent, 11, (0.,0.), angular=inertia*2.)
                    raw=dict(id=1,x=100.,y=0.,z=100.,yaw=0.,speed=0.,
                             tank_pushes=list(sent.values()))
                    worker._resolve_tank_contacts([raw], i*2., .1)
                    self.assertEqual([[1]+sent[11][1:]],state['contact_push_acks'])
                    if blocked:
                        self.assertEqual(before,state['yaw'])
                    else:
                        self.assertGreater(state['yaw'],before)
                    for j in range(50):
                        worker._resolve_tank_contacts([raw],i*2.+j*.02,.02)
                    self.assertEqual(0.,state['push_yaw'])
            self.assertTrue(worker._wreck_rotation_probe.called)

    def test_departed_wreck_falls_and_lands_after_horizontal_momentum_stops(self):
        worker=self._runtime(ground=-8.)
        worker.states.pop(12)
        state=self._wreck(worker)
        worker._apply_wreck_contact_response(state,dict(delta_velocity=(0.,2.),correction=(0.,0.)),.1)
        self.assertTrue(state['airborne'])
        state['push_x']=state['push_z']=0.
        previous=state['y']
        for i in range(120):
            worker._resolve_tank_contacts([],i*.02,.02)
            self.assertLessEqual(state['y'],previous)
            self.assertEqual(0,state['health'])
            self.assertFalse(state['alive'])
            previous=state['y']
        self.assertEqual(-8.,state['y'])
        self.assertFalse(state['airborne'])

    def test_airborne_wreck_keeps_horizontal_momentum_without_ground_friction(self):
        worker=self._runtime(ground=-100.)
        worker.states.pop(12)
        state=self._wreck(worker)
        state.update(airborne=True, push_z=2.,push_yaw=.3)
        worker._apply_wreck_contact_response(state,dict(delta_velocity=(0.,0.),correction=(0.,0.)),.1)
        self.assertEqual(2.,state['push_z'])
        self.assertEqual(.3,state['push_yaw'])
        self.assertAlmostEqual(.03,state['yaw'])

    def test_ten_spring_wreck_tips_off_supported_edge(self):
        # Use the same descriptor/spring adapter as the live cliff regression.
        worker,state,unused=bt.BotRuntimeTests._suspension_case(self,lambda x,z: 0. if z<=0. else -8.)
        worker.states={state['id']:state}
        state.update(x=0.,y=0.,z=-4.,alive=False,health=0,grounded_once=True,
                     mass=25000.,collision_shape=c.DEFAULT_SHAPE)
        idle=dict(delta_velocity=(0.,0.),correction=(0.,0.))
        for i in range(120):worker._apply_wreck_contact_response(state,idle,.02)
        departed=False
        for i in range(120):
            worker._apply_wreck_contact_response(state,dict(delta_velocity=(0.,.5),correction=(0.,0.)),.02)
            if state['airborne']:
                departed=True
                break
        self.assertTrue(departed)
        self.assertGreater(state['z'],-4.)
        self.assertLess(state['y'],0.)


class ForcedHazardTests(unittest.TestCase):
    setUp = bt.ShovedWreckTests.setUp
    tearDown = bt.ShovedWreckTests.tearDown

    def scenario(self, pushed=False, side=False, wall=False, water=False,
                 reverse=False, forecast='hazard'):
        worker=self.module.BotRuntime(1,
            descriptor_resolver=lambda unused:bt._combat_descriptor(),
            adapter_factory=lambda *args,**kwargs:bt._FixedAdapter(dict(
                throttle=1.,turn=0.,fire_allowed=False,movement_intent=True)),
            direction_probe=lambda *args,**kwargs:dict(clear=forecast=='clear',
                collision=forecast=='distant_wall',water=forecast=='hazard',slope=0.),
            ground_probe=lambda *args:0.,
            physics_ground_probe=lambda x,z,hint:0. if water or z<=1. else -100.,
            spawn_resolver=bt._spawn_resolver,baked_graph=bt._flat_open_graph(),
            motion_resolver=lambda *args,**kwargs:'hard' if wall else 'clear')
        worker.battle_start(dict(self.start,bots=self.start['bots'][:1]))
        state=worker.states[11]
        state.update(x=0.,y=0.,z=0.,yaw=math.pi if reverse else math.pi/2 if side else 0.,speed=0.,
                     grounded_once=True,push_x=0.,push_z=0.,movement_dir=0)
        worker._planner_corridor_clear=lambda *args,**kwargs:forecast=='clear'
        worker._water_depth_probe=lambda position:20. if water and position[2]>1. else -1.
        checkpoints={}
        if pushed:ledger.record(checkpoints,11,(0.,state['mass']*8.))
        player=dict(id=1,team=1,x=100.,y=0.,z=100.,yaw=0.,speed=0.,alive=True,
                    effective_params=bt._effective_params_snapshot(mass=100575.),
                    tank_pushes=list(checkpoints.values()))
        # Keep a fatal baked cell after the edge even when native world is clear.
        with mock.patch.object(self.module.prebaked_navigation,'pose_is_safe',
                               side_effect=lambda graph,pose,**kwargs:pose[2]<=1.), \
                mock.patch('sys.stdout'):
            for tick in range(480):
                worker.update(1./30.,tick/30.,[player])
                if not state['alive']:break
        return worker,state

    def test_forecast_and_realised_navigation_cannot_erase_external_travel(self):
        # A long planning ray may see a distant wall, or miss a fatal cell
        # reached this tick. Neither result can override the short physical
        # sweep of an already received shove.
        for forecast in ('clear', 'distant_wall'):
            for reverse in (False, True):
                with self.subTest(forecast=forecast, reverse=reverse):
                    unused, forced = self.scenario(pushed=True, forecast=forecast,
                                                   reverse=reverse)
                    self.assertGreater(forced['z'], 1.)
                    self.assertLess(forced['y'], -20.)
                    self.assertFalse(forced['alive'])
            unused, blocked = self.scenario(pushed=True, forecast=forecast, wall=True)
            self.assertAlmostEqual(0., blocked['z'])
            self.assertTrue(blocked['alive'])

    def test_driver_avoids_hazard_but_external_shove_can_fall_and_die(self):
        unused,unforced=self.scenario()
        self.assertLessEqual(unforced['z'],1.)
        self.assertTrue(unforced['alive'])
        for side,reverse in ((False,False),(True,False),(False,True)):
            with self.subTest(side=side,reverse=reverse):
                unused,forced=self.scenario(pushed=True,side=side,reverse=reverse)
                self.assertGreater(forced['z'],1.)
                self.assertLess(forced['y'],-20.)
                self.assertFalse(forced['alive'])

    def test_external_shove_into_deep_water_drowns_but_world_wall_still_blocks(self):
        unused,wet=self.scenario(pushed=True,water=True)
        self.assertGreater(wet['z'],1.)
        self.assertFalse(wet['alive'])
        self.assertTrue(wet.get('_drowned'))
        for side in (False,True):
            unused,blocked=self.scenario(pushed=True,side=side,wall=True)
            self.assertAlmostEqual(0.,blocked['z'])
            self.assertTrue(blocked['alive'])

    def test_braking_contact_is_not_relabelled_as_reverse_external_drive(self):
        retain=self.module.BotRuntime._retained_contact_speed
        self.assertEqual(0.,retain(1.,-4.))
        self.assertEqual(0.,retain(-1.,4.))
        self.assertEqual(-2.,retain(-2.,-7.))
        self.assertEqual(2.,retain(2.,7.))
        self.assertEqual(0.,retain(0.,7.))

    def test_passive_shove_still_respects_the_arena_rectangle(self):
        worker=bt.ShovedWreckTests._runtime(self)
        worker.states.pop(12)
        state=worker.states[11]
        worker.baked_graph=dict(worker.baked_graph,bounds=(-20.,-20.,20.,5.))
        worker.motion_resolver=lambda *args,**kwargs:'clear'
        state.update(x=0.,y=0.,z=1.5,yaw=0.,speed=0.,half_length=3.5,
                     half_width=1.7,grounded_once=True,push_x=0.,push_z=0.)
        worker._apply_tank_contact_response(state,dict(
            delta_velocity=(0.,8.),correction=(0.,0.)),.1,
            advance_push=False,advance_forward=True)
        self.assertEqual(1.5,state['z'])
        self.assertEqual(0.,state['speed'])


class HeadOnOwnerTests(unittest.TestCase):
    setUp = bt.ShovedWreckTests.setUp
    tearDown = bt.ShovedWreckTests.tearDown
    _runtime = bt.ShovedWreckTests._runtime

    def travel(self, human_mass, human_hp, bot_mass, bot_hp, hz,
               delay=0.0, reverse=False, prediction=True, input_delay=0.0,
               human_throttle=1, bot_throttle=1, drop_edge=None, alive=True):
        worker=self._runtime()
        worker.states.pop(12)
        worker.baked_graph=bt._flat_open_graph()
        worker.adapter=bt._FixedAdapter(dict(throttle=bot_throttle,turn=0.,fire_allowed=False))
        state=worker.states[11]
        state.update(x=0.,y=0.,z=6.99,yaw=math.pi,speed=0.,mass=bot_mass,
                     pitch=0.,roll=0.,push_x=0.,push_z=0.,grounded_once=True,
                     collision_shape=c.DEFAULT_SHAPE,movement_dir=bot_throttle)
        if drop_edge is not None:
            state.update(alive=alive,health=500 if alive else 0)
            worker._physics_ground_probe=lambda x,z,hint:0. if z<=drop_edge else -30.
            worker._ground_probe=lambda x,z,*unused:0. if z<=drop_edge else -30.
            spring_descriptor=bt._suspension_descriptor()
            spring_descriptor.physics['weight']=bot_mass
            def spring_ground(x,z,minimum_y,maximum_y,flat_maximum_y=None):
                height=0. if z<=drop_edge else -30.
                if (minimum_y-.01<=height<=maximum_y+.01 and
                        (flat_maximum_y is None or height<=flat_maximum_y)):
                    return height
                return None
            worker._wreck_ground_probe=spring_ground
            worker._suspension_params[11]=self.module.vehicle_physics.derive_suspension_params(spring_descriptor)
        params=worker._physics_params_for(11)
        params.update(mass=bot_mass,powerW=bot_hp*735.49875)
        human_params=dict(params,mass=human_mass,powerW=human_hp*735.49875)
        worker._player_collision_profile=lambda raw:dict(mass=human_mass,
                shape=c.DEFAULT_SHAPE,ram_profile={},physics=human_params)
        native=vt._runtime();battle=vt.BattleRuntime(native)
        battle.client=vt._Client();battle._avatar=native.bigworld.avatar
        battle._local_physics=human_params
        replica=self._runtime()
        replica.battle_start(dict(self.start,round_id=6,bot_authority_id=-1))
        self.assertFalse(replica.states)
        self.assertFalse(replica._descriptors)
        battle._bots=replica
        clock=[0.0]
        battle._clock=lambda:clock[0]
        battle._estimated_motion_time_us=lambda unused:int(clock[0]*1000000)
        battle._ram_bot_history_index={11:[(0,0)]}
        battle._ram_bot_history={0:{11:copy.deepcopy(state)}}
        local=vt._Vehicle(10,vt._Descriptor(),vt._Vector(),(0,0,0),{'health':500})
        battle._collision_shape=lambda unused:c.DEFAULT_SHAPE
        battle._motion_is_clear=lambda *args,**kw:True
        battle._baked_pose_safe=lambda *args:True
        battle._poll_local_ram_contact_episodes=lambda *args:None
        published=copy.deepcopy(state)
        descriptor=vt._Descriptor()
        descriptor.physics.update(weight=bot_mass,enginePower=bot_hp*735.49875,
            specificFriction=params['specificFriction'],terrainResistance=params['terrainResist'],
            speedLimits=(params['speedFwd'],params['speedBwd']))
        native.bigworld.entities[11]=vt._Vehicle(11,descriptor,vt._Vector(),(0,0,0),{'health':500})
        record=dict(engine_id=11,network_id=11,kind='bot',local=False,ready=True,
                    tombstone=False,state=published,presented_pose=published)
        battle._records={'bot:11':record}
        def others(*args,**kw):
            pending=ledger.pending(battle._local_contact_pushes,11,published.get('contact_push_acks'),battle.client.player_id)
            peer=_tank(1000011,0.,published['z'],yaw=math.pi,mass=bot_mass,
                       vz=-published['speed']+published.get('push_z',0.)+pending[1]/bot_mass)
            if prediction:
                peer['vz']=battle._predict_bot_contact_velocity(11,published,c.DEFAULT_SHAPE,params)[1]
            peer.update(kind='bot',network_id=11,physical_velocity=(0.,peer['vz']),
                        contact_decel=self.module.vehicle_physics.contact_push_decel(params,True))
            return [peer]
        if not prediction:
            battle._contact_tanks=others
        position=(0.,0.,0.);dt=1./hz;bank=0.;queue=[]
        human_queue=[];published_human=None
        sign=-1 if reverse else 1
        human_yaw=math.pi if reverse else 0.
        with mock.patch('sys.stdout'):
            for tick in range(hz*6):
                clock[0]=(tick+1)*dt
                battle._local_speed=self.module.vehicle_physics.longitudinal_step(
                    human_params,battle._local_speed,sign*human_throttle,False,0.,dt)
                start=position
                position=battle._resolve_local_tank_contacts(local,
                    (0.,0.,position[2]+battle._local_speed*dt*sign),human_yaw,dt,start_position=start)
                bank+=dt
                if bank+1.e-9>=.1:
                    raw=dict(id=battle.client.player_id,team=1,x=0.,y=0.,z=position[2],
                             yaw=human_yaw,speed=battle._local_speed,forward=sign*human_throttle,
                             tank_pushes=copy.deepcopy(list(battle._local_contact_pushes.values())))
                    human_queue.append((clock[0]+input_delay,raw))
                    while human_queue and human_queue[0][0]<=clock[0]+1.e-9:
                        unused,published_human=human_queue.pop(0)
                    if published_human is None:
                        published_human=dict(raw,z=0.,speed=0.,tank_pushes=[])
                    raw=published_human
                    # Exercise the real drive/passive split, navigation pose
                    # guards, human receipts and complete contact pass.
                    raw=bt._admit_player(raw,mass=human_mass)
                    worker._update_once(bank,clock[0],[raw])
                    queue.append((clock[0]+delay,int(clock[0]*1000000),tick+1,copy.deepcopy(state)))
                    bank=0.
                while queue and queue[0][0]<=clock[0]+1.e-9:
                    unused,stamp,revision,published=queue.pop(0)
                    record['state']=published
                    record['presented_pose']=published
                    battle._ram_bot_history_index={11:[(stamp,revision)]}
                    battle._ram_bot_history={revision:{11:published}}
        if drop_edge is not None:
            self.assertFalse(worker._suspension_param_failures)
            return state
        return state['z']-6.99

    def test_separate_owners_can_shove_live_and_dead_hulls_off_a_supported_edge(self):
        for alive in (True,False):
            with self.subTest(alive=alive):
                state=self.travel(100575.,1200.,23496.,520.,60,delay=.15,
                    input_delay=.15,drop_edge=9.,alive=alive)
                self.assertGreater(state['z'],9.)
                self.assertLess(state['y'],-20.)

    def test_empty_authority_descriptor_cache_reproduces_report_head_on_deadlock(self):
        original=vt.BattleRuntime._predict_bot_contact_velocity
        def old_cache(runtime,bot_id,state,shape,unused_params):
            return original(runtime,bot_id,state,shape,
                            self.module.vehicle_physics.derive_params({}))
        with mock.patch.object(vt.BattleRuntime,'_predict_bot_contact_velocity',old_cache):
            blocked=self.travel(100575.,1200.,55883.,800.,60,delay=.15,input_delay=.15)
        released=self.travel(100575.,1200.,55883.,800.,60,delay=.15,input_delay=.15)
        self.assertAlmostEqual(0.,blocked)
        self.assertGreater(released,1.)

    def test_bidirectional_delay_does_not_turn_head_on_power_into_a_deadlock(self):
        for hz in (30,60,144):
            for delay in (.1,.2,.3):
                with self.subTest(hz=hz,delay=delay):
                    self.assertGreater(self.travel(100575.,1200.,55883.,800.,hz,
                        delay=delay,input_delay=delay),1.)
                    self.assertLess(self.travel(21100.,600.,35500.,600.,hz,
                        delay=delay,input_delay=delay),-.1)
                    self.assertGreater(self.travel(35500.,600.,21100.,600.,hz,
                        delay=delay,input_delay=delay,bot_throttle=0),1.)

    def test_kv5_outpushes_su100m1_with_either_player_owner(self):
        for hz in (30,60,144):
            with self.subTest(hz=hz):
                self.assertGreater(self.travel(100575.,1200.,32000.,520.,hz),1.)
                self.assertLess(self.travel(32000.,520.,100575.,1200.,hz),-1.)

    def test_same_mass_with_weaker_engine_cannot_gain_the_same_push(self):
        strong=self.travel(100575.,1200.,32000.,520.,60)
        weak=self.travel(100575.,100.,32000.,520.,60)
        self.assertGreater(strong,weak)

    def test_report_reverse_push_survives_publication_delay(self):
        # 152755: KV-5 against T-54 first prototype, ~90 Hz visible / 10 Hz worker.
        # Permanent pending velocity reproduces the zero-displacement deadlock.
        self.assertAlmostEqual(0.,self.travel(100575.,1200.,35500.,760.,90,
                                             delay=.1,reverse=True,prediction=False))
        for hz in (30,90,144):
            for delay in (.1,.2,.3):
                with self.subTest(hz=hz,delay=delay):
                    self.assertGreater(self.travel(100575.,1200.,35500.,760.,hz,
                                                   delay,True),1.)
        self.assertLess(self.travel(35500.,760.,100575.,1200.,90,.2),-1.)


if __name__=='__main__':unittest.main()
