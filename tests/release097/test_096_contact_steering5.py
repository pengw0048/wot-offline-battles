"""Contact-steering test5. Engine fakes are not a Windows gameplay test."""
import math
import random
import types
import unittest
from unittest import mock

import test_port_0922_battle_runtime as local
from gui.mods.offline_lan_0922 import vehicle_physics as vp
from gui.mods.offline_lan_0922 import tank_collision as tc
from gui.mods.offline_lan_0922 import battle_runtime as br


def params(mass=129860., hp=1200.):
    return dict(vp._DEFAULTS, mass=mass, powerW=hp*735.49875,
                rotSpd=math.radians(22.0))


def body(i, x=0., z=0., mass=36000., shape=(1.5, 3.5, -.8, 2.), **kw):
    b=dict(id=i,x=x,y=0.,z=z,mass=mass,yaw=0.,shape=shape,
           vx=0.,vz=0.,alive=True,network_id=i,kind='bot')
    b.update(kw)
    return b


class SharedPowerTests(unittest.TestCase):
    def test_full_drive_does_not_algebraically_erase_turn_torque(self):
        for speed in (0.,1.5,3.,5.3193,-3.):
            for drive in (-1., 1.):
                omega,tau=vp.contact_traverse(params(),1.5,speed,1.,.04,drive)
                self.assertGreater(tau,0.)
                self.assertEqual(math.copysign(1.,omega),drive)

    def test_power_and_traction_are_shared_not_duplicated(self):
        for hp in (300.,1200.,2400.):
            p=params(hp=hp)
            for speed in (0.,.25,1.5,3.,20.):
                for drive in (-1.,-.5,.5,1.):
                    for turn in (-1.,-.3,.3,1.):
                        omega,tau=vp.contact_traverse(p,1.5,speed,turn,.04,drive)
                        f=abs(vp.engine_force(p,speed,drive))*vp.steering_drive_scale(drive,turn)
                        power=p['powerW']*vp.POWER_FACTOR*p.get('nativePowerRatio',1.)
                        self.assertLessEqual(f*speed+tau*abs(omega),power+1e-7)
                        self.assertLessEqual(f+tau/1.5,p['mass']*vp.GRAVITY+1e-7)

    def test_longitudinal_consumer_uses_same_share(self):
        p=params(); speed=2.; dt=.01
        expected=speed+(vp.engine_force(p,speed,1.)*.5-
                       vp.rolling_resist_force(p,steering=True))/p['mass']*dt
        self.assertAlmostEqual(expected,vp.longitudinal_step(p,speed,1.,True,0.,dt))

    def test_straight_drive_does_not_lose_power(self):
        p=params();speed=2.;dt=.01
        expected=speed+(vp.engine_force(p,speed,1.)-
                       vp.rolling_resist_force(p))/p['mass']*dt
        self.assertAlmostEqual(expected,vp.longitudinal_step(p,speed,1.,False,0.,dt))

    def test_more_power_helps_until_traction_cap(self):
        torques=[vp.contact_traverse(params(hp=hp),1.5,3.,1.,.04,1.)[1]
                 for hp in (0.,600.,1200.,2400.)]
        self.assertEqual(torques[0],0.)
        self.assertTrue(all(b>a for a,b in zip(torques,torques[1:])))

    def test_no_turn_means_no_extra_force(self):
        self.assertEqual((0.,0.),vp.contact_traverse(params(),1.5,3.,0.,.04,1.))
        self.assertEqual((0.,0.),vp.contact_traverse(params(),1.5,3.,1.,0.,1.))


class ActualPatchTests(unittest.TestCase):
    def test_long_hull_contact_is_inside_short_peer(self):
        a=body(1,shape=(2.,5.,-.8,2.)); b=body(2,x=3.5,shape=(1.5,2.5,-.8,2.))
        for sign in (-1.,1.):
            p=tc.traverse_contact_point(a,b,(-1.,0.),sign)
            self.assertAlmostEqual(2.,p[0])
            self.assertAlmostEqual(2.5*sign,p[1])
            self.assertLessEqual(abs(p[1]-b['z']),b['shape'][1]+1e-7)

    def test_small_touch_gap_gets_contact_without_enlarging_motion_shape(self):
        a=body(1);b=body(2,x=3.005)
        shape=b['shape']
        self.assertIsNotNone(tc.traverse_contact_point(a,b,(-1.,0.),1.))
        self.assertEqual(shape,b['shape'])
        b['x']=3.1
        self.assertIsNone(tc.traverse_contact_point(a,b,(-1.,0.),1.))

    def test_side_force_is_reciprocal_with_real_peer_mass(self):
        a=body(1,mass=129860.,shape=(2.,5.,-.8,2.));b=body(2,x=3.5,shape=(1.5,2.5,-.8,2.))
        a['traverse_speed'],a['traverse_torque']=vp.contact_traverse(params(),2.,0.,1.,.04)
        result=tc.traverse_impulses([a,b],.04)
        self.assertGreater(result[2][0],0.)
        self.assertAlmostEqual(0.,a['mass']*result[1][0]+b['mass']*result[2][0])
        spent=result[2][0]*b['mass']*2.5
        self.assertLessEqual(spent,a['traverse_torque']*.04+1e-7)

    def test_disabled_motor_does_not_push(self):
        a=body(1,mass=129860.);b=body(2,x=3.)
        a['traverse_speed'],a['traverse_torque']=vp.contact_traverse(params(hp=0.),1.5,0.,1.,.04)
        self.assertEqual((0.,0.),tc.traverse_impulses([a,b],.04)[2])

    def test_pure_turn_pushes_stationary_shorter_light_hull_for_both_signs(self):
        for sign in (-1.,1.):
            a=body(1,mass=129860.,shape=(2.,5.,-.8,2.));b=body(2,x=3.5,shape=(1.5,2.5,-.8,2.))
            a['traverse_speed'],a['traverse_torque']=vp.contact_traverse(params(),2.,0.,sign,.04)
            for bdy in (a,b):
                bdy['contact_decel']=vp.contact_push_decel(params(mass=bdy['mass']),False)
            delta=tc.traverse_impulses([a,b],.04)[2]
            after=vp.contact_push_step(params(mass=b['mass']),*delta,0.,.04)
            self.assertGreater(after[0],0.)


class FixedReactionTests(unittest.TestCase):
    def solve(self,**kw):
        args=dict(p=params(),shape=(1.5,3.5,-.8,2.),position=(0.,0.,0.),yaw=0.,
                  turn=1.,speed=0.,drive_intent=0.,dt=.04,
                  hit=(1.5,.6,1.),normal=(-1.,0.,0.))
        args.update(kw)
        return vp.fixed_contact_turn(**args)

    def test_fixed_wall_can_move_self_without_moving_wall(self):
        a=self.solve()
        self.assertGreater(a['angle'],0.)
        self.assertLess(a['translation'][0],0.)
        self.assertEqual(0.,a['translation'][1])

    def test_both_steer_signs_remain_outside_same_side_plane(self):
        for sign in (-1.,1.):
            a=self.solve(turn=sign)
            for k in range(101):
                t=k/100.;angle=a['angle']*t
                xmax=a['translation'][0]*t+1.5*math.cos(angle)+3.5*abs(math.sin(angle))
                self.assertLessEqual(xmax,1.5+1e-9)

    def test_force_is_torque_and_mass_limited(self):
        lo=self.solve(p=params(hp=600.)); hi=self.solve(p=params(hp=1200.))
        heavy=self.solve(p=params(mass=180000.,hp=1200.))
        self.assertGreater(hi['angle'],lo['angle'])
        self.assertGreater(hi['angle'],heavy['angle'])
        self.assertIsNone(self.solve(p=params(hp=0.)))
        self.assertIsNone(self.solve(p=params(hp=10.)))

    def test_recoil_energy_cannot_exceed_motor_work(self):
        a=self.solve()
        energy=.5*a['effective_inertia']*a['rate']**2
        work=a['net_torque']*abs(a['angle'])
        self.assertLessEqual(energy,work+1e-8)
        self.assertLessEqual(a['torque']*abs(a['rate']),params()['powerW']*vp.POWER_FACTOR)

    def test_no_remote_repulsion_no_ground_no_underside_no_input(self):
        self.assertIsNone(self.solve(hit=(2.,.6,1.)))
        self.assertIsNone(self.solve(normal=(0.,1.,0.)))
        self.assertIsNone(self.solve(normal=(0.,-1.,0.)))
        self.assertIsNone(self.solve(turn=0.))
        self.assertIsNone(self.solve(dt=0.))

    def test_non_finite_input_rejected(self):
        self.assertIsNone(self.solve(normal=(float('nan'),0.,0.)))

    def test_asymmetric_world_footprint_uses_installed_bounds(self):
        a=self.solve(shape=(1.5,3.5,-.8,2.),bounds=(-2.,2.,4.,5.),hit=(2.,.6,0.))
        self.assertIsNotNone(a)
        self.assertGreater(a['arm'],5.)

    def test_direction_reversal_cannot_keep_old_angular_velocity(self):
        a=self.solve(previous_rate=-.35)
        b=self.solve(previous_rate=0.)
        self.assertEqual(a,b)


class JointSweepTests(unittest.TestCase):
    def test_analytic_translated_extrema_agrees_with_dense_independent_sampling(self):
        rng=random.Random(9351)
        for unused in range(500):
            a,b=(rng.uniform(-10,10) for _ in range(2))
            start=rng.uniform(-6,6);end=start+rng.uniform(-1.5,1.5);drift=rng.uniform(-3,3)
            exact=br._trig_linear_interval_minimum(a,b,start,end,drift)
            sampled=min(a*math.cos(start+(end-start)*k/2000.)+
                        b*math.sin(start+(end-start)*k/2000.)+drift*k/2000.
                        for k in range(2001))
            self.assertLessEqual(exact,sampled+1e-10)
            self.assertLess(sampled-exact,2e-6)

    def test_coupled_recoil_releases_occupied_plane_not_a_new_wall(self):
        p=params();c=vp.fixed_contact_turn(p,(1.5,3.5,-.8,2.),(0.,0.,0.),0.,1.,0.,0.,.1,
                                         (1.5,.6,1.),(-1.,0.,0.))
        fn=br._rotation_departing_contact((0.,0.,0.),((-1.5,-.8,-3.5),(1.5,2.,3.5)),
                                         0.,c['angle'],translation=c['translation'])
        self.assertTrue(fn((local._Vector(1.5,.6,1.),local._Vector(-1.,0.,0.))))
        self.assertFalse(fn((local._Vector(-1.6,.6,1.),local._Vector(1.,0.,0.))))

    def test_peer_geometry_checks_translation_and_angle_together(self):
        a=vp.fixed_contact_turn(params(),(1.5,3.5,-.8,2.),(0.,0.,0.),0.,1.,0.,0.,.1,
                                (1.5,.6,1.),(-1.,0.,0.))
        b=body(2,x=3.)
        f=tc.rotation_fraction((0.,0.,0.),0.,a['angle'],(1.5,3.5,-.8,2.),[b],translation=a['translation'])
        self.assertEqual(1.,f)
        blockers=[b,body(3,x=-3.)]
        f=tc.rotation_fraction((0.,0.,0.),0.,a['angle'],(1.5,3.5,-.8,2.),blockers,translation=a['translation'])
        self.assertLess(f,1.)

    def test_pose_adapter_preserves_translation_for_native_guard(self):
        from test_port_0922_siege_contacts import battle_fixture
        battle,entity,*unused=battle_fixture()
        battle._destructible_pose_sweep=mock.Mock(return_value={'status':'clear','token':None})
        battle._native_world_rotation_is_clear=mock.Mock(return_value=True)
        self.assertTrue(battle._pose_sweep_is_clear(entity,(0.,0.,0.),0.,(-.1,0.,0.),.01,0.,.1))
        kw=battle._native_world_rotation_is_clear.call_args.kwargs
        self.assertAlmostEqual(-.1,kw['translation'][0])
        self.assertEqual(0.,kw['translation'][1])
        self.assertTrue(kw['include_static'])

    def test_native_joint_sweep_checks_static_world_before_destruction(self):
        rt=local._runtime(); battle=br.BattleRuntime(rt);battle._avatar=rt.bigworld.avatar
        battle._destructibles=types.SimpleNamespace(
            native_replacement_bsp_active=lambda:False,
            _vehicle_body_bbox=lambda td:((-1.5,-.8,-3.5),(1.5,2.,3.5)))
        with mock.patch.object(br.world_collision,'check_horizontal_collision',return_value='hard') as query:
            self.assertFalse(battle._native_world_rotation_is_clear((0,0,0),0,.01,{},translation=(-.1,0.)))
        self.assertGreater(query.call_count,0)


class LocalAdapterTests(unittest.TestCase):
    def setup_actor(self,blocked=False):
        rt=local._runtime();b=br.BattleRuntime(rt);b._avatar=rt.bigworld.avatar;b.client=local._Client()
        d=local._Descriptor();d.physics.update(weight=129860.,enginePower=1200.*735.49875)
        e=local._Vehicle(10,d,local._Vector(),(0,0,0),{'health':500})
        b._local_physics=vp.derive_params(d);b._sender=types.SimpleNamespace(forward=0.)
        b._local_drive_throttle=0.;b._local_drive_turn=1.
        b._contact_tanks=lambda *a,**kw:[]
        b._arena_rotation_is_clear=mock.Mock(return_value=not blocked)
        b._pose_sweep_is_clear=mock.Mock(return_value=not blocked)
        bounds=br.world_collision._vehicle_motion_bounds(d)
        trace={'hit':(bounds[1],.6,0.),'normal':(-1.,0.,0.)}
        return b,e,trace

    def test_fixed_candidate_reaches_complete_runtime_pose_gate(self):
        b,e,t=self.setup_actor()
        out=b._recover_local_world_turn(e,(0.,0.,0.),0.,1.,.04,t)
        self.assertIsNotNone(out)
        self.assertLess(out[0][0],0.)
        self.assertGreater(out[1],0.)
        self.assertTrue(b._local_contact_reaction_paid)
        call=b._pose_sweep_is_clear.call_args
        self.assertLess(call.args[3][0],call.args[1][0])
        self.assertGreater(call.args[4],call.args[2])

    def test_blocked_recoil_does_not_keep_angular_momentum(self):
        b,e,t=self.setup_actor(blocked=True)
        out=b._recover_local_world_turn(e,(0.,0.,0.),0.,1.,.04,t)
        self.assertIsNone(out)
        self.assertEqual(0.,b._local_reaction_rate)
        self.assertFalse(getattr(b,'_local_contact_reaction_paid',False))

    def test_stationary_peer_can_hold_and_redirect_self_but_moving_peer_cannot(self):
        b,e,t=self.setup_actor()
        peer=body(2,x=4.,mass=36000.,contact_decel=(6.,6.))
        out=b._recover_local_world_turn(e,(0.,0.,0.),0.,1.,.04,t,held_peer=peer)
        self.assertIsNotNone(out)
        peer['vx']=1.
        self.assertIsNone(b._recover_local_world_turn(e,(0.,0.,0.),0.,1.,.04,t,held_peer=peer))

    def test_full_local_contact_consumes_torque_once_after_recoil(self):
        b,e,t=self.setup_actor()
        b._local_contact_reaction_paid=True
        b._local_contact_start_position=(0.,0.,0.)
        b._poll_local_ram_contact_episodes=lambda *a:None
        b._motion_is_clear=lambda *a,**kw:True
        b._baked_pose_safe=lambda *a:True
        seen=[]
        def observe(tanks,dt,**kw):
            seen.extend(tanks)
            return {t['id']:(0.,0.) for t in tanks}
        with mock.patch.object(tc,'traverse_impulses',side_effect=observe):
            out=b._resolve_local_tank_contacts(e,(-.02,0.,0.),.01,.04)
        self.assertEqual((-0.02,0.,0.),out)
        self.assertEqual(0.,seen[0]['traverse_torque'])
        self.assertFalse(b._local_contact_reaction_paid)

    def test_finite_peer_receipt_still_uses_existing_ledger(self):
        b,e,t=self.setup_actor()
        shape=b._collision_shape(e.typeDescriptor)
        peer=body(2,x=2.*shape[0],mass=10000.,shape=shape)
        b._contact_tanks=lambda *a,**kw:[peer]
        b._poll_local_ram_contact_episodes=lambda *a:None
        b._run_optional_feature=lambda *a,**kw:None
        b._motion_is_clear=lambda *a,**kw:True;b._baked_pose_safe=lambda *a:True
        b._local_speed=3.;b._local_drive_throttle=1.
        b._resolve_local_tank_contacts(e,(0.,0.,0.),0.,.04)
        self.assertIn(2,b._local_contact_pushes)
        self.assertGreater(b._local_contact_pushes[2][2],0.)




class DriveLoopTests(unittest.TestCase):
    def actor(self, turn=1., hp=1200.):
        rt=local._runtime(); battle=br.BattleRuntime(rt)
        battle.client=local._Client();battle._avatar=rt.bigworld.avatar
        desc=local._Descriptor();desc.physics.update(weight=129860.,enginePower=hp*735.49875)
        e=local._Vehicle(10,desc,local._Vector(),(0,0,0),{'health':500})
        rt.bigworld.entities[10]=e
        battle._server=types.SimpleNamespace(vehicle_id=10)
        battle._sender=types.SimpleNamespace(forward=0.,turn=turn,handbrake=False,
                                             send_current=mock.Mock(return_value=True))
        battle._local_descriptor=desc;battle._attach_local_presentation()
        battle._smoothed_drive_pitch=lambda *a:0.
        battle._ensure_local_suspension_params=lambda *a:None
        battle._update_vertical_motion=lambda e,p,y,dt:p
        battle._ground_pitch=lambda *a:0.
        battle._apply_slope_slide=lambda p,y,dt,e=None:p
        battle._resolve_local_tank_contacts=lambda e,p,y,dt:p
        battle._contact_tanks=lambda *a,**kw:[]
        battle._destructible_pose_sweep=lambda *a,**kw:{'status':'clear','token':None}
        battle._destructibles=types.SimpleNamespace(
            native_replacement_bsp_active=lambda:False,
            _vehicle_body_bbox=lambda td:((-1.5,-.8,-3.5),(1.5,2.,3.5)))
        return rt,battle,e

    def scene(self,rt,two_walls=False):
        def collide(space,start,end,flags,*args):
            hits=[]
            walls=[(1.5,-1.)]+([(-1.5,1.)] if two_walls else [])
            for x,n in walls:
                if abs(end.x-start.x)>1e-10:
                    f=(x-start.x)/(end.x-start.x)
                    if 0.<=f<=1.:
                        pt=start+(end-start).scale(f)
                        if -.8<=pt.y<=3.:
                            hits.append((f,(pt,local._Vector(n,0.,0.),0)))
            if end.y<start.y and end.y<=0.<=start.y:
                f=-start.y/(end.y-start.y)
                hits.append((f,(start+(end-start).scale(f),local._Vector(0.,1.,0.),0)))
            return min(hits,key=lambda v:v[0])[1] if hits else None
        rt.bigworld.wg_collideSegment=collide
        return collide

    def test_real_drive_loop_and_analytic_native_wall_recoil_both_signs(self):
        for sign in (-1.,1.):
            rt,b,e=self.actor(sign);self.scene(rt)
            with mock.patch.object(br.world_collision,'_destroy_and_recast',return_value=False):
                b._drive_local_step(.04)
            self.assertLess(b._local_position[0],0.)
            self.assertGreater(b._local_yaw*sign,0.)
            xmax=b._local_position[0]+1.5*math.cos(b._local_yaw)+3.5*abs(math.sin(b._local_yaw))
            self.assertLessEqual(xmax,1.5+1e-8)

    def test_real_drive_loop_two_native_walls_prevent_recoil(self):
        rt,b,e=self.actor();self.scene(rt,True)
        with mock.patch.object(br.world_collision,'_destroy_and_recast',return_value=False):
            b._drive_local_step(.04)
        self.assertEqual((0.,0.,0.),b._local_position)
        self.assertEqual(0.,b._local_yaw)

    def test_catalog_pending_cannot_reuse_stale_wall_contact(self):
        rt,b,e=self.actor()
        b._local_world_collision_trace={'hit':(1.5,.6,0.),'normal':(-1.,0.,0.)}
        b._destructible_pose_sweep=lambda *a,**kw:{'status':'pending','token':None}
        b._drive_local_step(.04)
        self.assertEqual((0.,0.,0.),b._local_position)
        self.assertEqual(0.,b._local_yaw)
        self.assertEqual(0.,b._local_reaction_rate)

    def test_support_reject_cannot_keep_a_recoiled_yaw(self):
        rt,b,e=self.actor();self.scene(rt)
        def rejected(entity,p,y,dt):
            b._local_support_rise_blocked=True
            return (0.,0.,0.)
        b._update_vertical_motion=rejected
        with mock.patch.object(br.world_collision,'_destroy_and_recast',return_value=False):
            b._drive_local_step(.04)
        self.assertEqual((0.,0.,0.),b._local_position)
        self.assertEqual(0.,b._local_yaw)
        self.assertEqual(0.,b._local_reaction_rate)

    def test_drive_loop_heavy_side_contact_keeps_finite_peer_push(self):
        for drive in (0.,1.):
            rt,b,e=self.actor()
            shape=b._collision_shape(e.typeDescriptor)
            peer=body(11,x=2.*shape[0],mass=36000.,shape=shape,
                      contact_decel=vp.contact_push_decel(params(mass=36000.),False))
            b._contact_tanks=lambda *a,**kw:[peer]
            b._sender.forward=drive;b._local_speed=3. if drive else 0.
            b._resolve_local_tank_contacts=types.MethodType(br.BattleRuntime._resolve_local_tank_contacts,b)
            b._poll_local_ram_contact_episodes=lambda *a:None
            b._run_optional_feature=lambda *a,**kw:None
            b._motion_is_clear=lambda *a,**kw:True;b._baked_pose_safe=lambda *a:True
            b._pose_sweep_is_clear=lambda *a,**kw:True
            b._drive_local_step(.04)
            if drive == 0.:
                self.assertGreater(b._local_contact_pushes[11][2],0.)
            else:
                self.assertTrue(b._local_position[0]<0. or
                                b._local_contact_pushes.get(11,[0,0,0])[2]>0.)
            self.assertFalse(b._local_contact_reaction_paid)

    def test_drive_loop_no_motor_no_free_recoil(self):
        rt,b,e=self.actor(hp=0.);self.scene(rt)
        with mock.patch.object(br.world_collision,'_destroy_and_recast',return_value=False):
            b._drive_local_step(.04)
        self.assertEqual((0.,0.,0.),b._local_position)
        self.assertEqual(0.,b._local_yaw)


if __name__ == '__main__':
    unittest.main()
