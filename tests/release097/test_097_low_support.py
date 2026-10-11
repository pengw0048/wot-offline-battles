from __future__ import print_function
"""Test8L support ablation contracts; synthetic terrain, no native game."""
import os, sys, math, types, copy, unittest
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PKG = os.path.join(ROOT, 'src/res/scripts/client/gui/mods/offline_lan_0922')
for name in ('gui', 'gui.mods', 'gui.mods.offline_lan_0922'):
    module = types.ModuleType(name); module.__path__ = [PKG]; sys.modules[name] = module
module = types.ModuleType('gui.mods.offline_lan_0922.ai')
module.__path__ = [os.path.join(PKG, 'ai')]; sys.modules[module.__name__] = module
from gui.mods.offline_lan_0922 import bot_runtime as br, vehicle_physics as vp

class Obj(object):
    def __init__(self, **kw): self.__dict__.update(kw)
class Component(Obj):
    def forbidden(self, *a, **kw): raise AssertionError('native component is attribute-only')
    get = __getitem__ = __contains__ = forbidden

def descriptor():
    chassis = Component(hitTester=Obj(bbox=((-1.5,-.8,-3.5),(1.5,.8,3.5),None)),
        hullPosition=(0.,.6,0.), rotationSpeed=.75, topRightCarryingPoint=(1.5,3.5),
        shotDispersionFactors=(.14,.14), maxHealth=170, maxRegenHealth=130,
        name='testChassis')
    hull = Component(hitTester=Obj(bbox=((-1.7,-.2,-3.5),(1.7,1.4,3.5),None)),
        turretPositions=((0.,1.,0.),))
    gun = Obj(shots=({'shell':{'effectsIndex':0},'speed':1000.,'gravity':10.,'maxDistance':5000.},),
        reloadTime=.5,clip=(2,.2),turretYawLimits=(-math.pi,math.pi),
        pitchLimits={'absolute':(-.35,.15)},rotationSpeed=10.,shotDispersionAngle=.03,
        maxHealth=54,maxRegenHealth=27)
    return Obj(chassis=chassis,hull=hull,gun=gun,radio=Obj(distance=700.),
        turret={'rotationSpeed':10.,'circularVisionRadius':445.},maxHealth=1000,
        physics={'speedLimits':(14.,7.),'weight':25000.,'enginePower':500000.,
            'terrainResistance':(1.,1.,1.),'specificFriction':1.,'trackCenterOffset':1.35},
        type=Obj(xphysics={'detailed':{'chassis':{'testChassis':{
            'roadWheelPositions':(-2.4,-1.2,0.,1.2,2.4),'stiffnessFactors':(1.,)*5,
            'stiffness0':1.,'stiffness1':1.,'damping':.2,'bodyHeight':1.4,
            'hullInertiaFactors':(1.,1.,1.8),'wheelRadius':.4}}}}))

def fixture(alive=True, ground=None):
    counts={'centre':0,'detailed':0}
    terrain=ground or (lambda x,z,h:0.)
    def centre(x,z,h): counts['centre']+=1; return terrain(x,z,h)
    def detailed(x,z,lo,hi,flat_maximum_y=None):
        counts['detailed']+=1
        value=terrain(x,z,hi)
        if value is None or not lo-.01 <= value <= hi+.01: return None
        if not vp.suspension_support_allowed(value,1.,flat_maximum_y):return None
        return value
    w=br.BotRuntime(1,physics_ground_probe=centre,wreck_ground_probe=detailed)
    s=dict(id=11,x=0.,y=0.,z=0.,yaw=0.,speed=0.,half_length=3.5,half_width=1.5,
        movement_dir=0,rotation_dir=0,push_x=0.,push_z=0.,push_yaw=0.,
        air_lateral_x=0.,air_lateral_z=0.,slide_speed=0.,vertical_speed=0.,
        airborne=False,grounded_once=True,last_drive_pitch=0.,pitch=0.,
        terrain_pitch=0.,roll=0.,suspension_pitch=0.,suspension_pitch_velocity=0.,
        suspension_roll_velocity=0.,alive=alive,health=1000,max_health=1000,
        collision_shape=(1.5,3.5,-.5,2.),mass=25000.)
    w.states={11:s};w._descriptors[11]=descriptor();w._turn_speeds[11]=0.
    w._contact_motion_bodies=lambda players:[]
    return w,s,counts

def step(w,s,dt=.2):
    return w._update_vertical_motion(s,dt,(s['x'],s['y'],s['z']),s['yaw'])

def forbidden(*a,**kw):raise AssertionError('detailed Bot support called')

class LowSupportTests(unittest.TestCase):
    def test_build_switch_is_off(self):self.assertFalse(br.BOT_FULL_SUPPORT_ENABLED)
    def test_untouched_live(self):
        w,s,c=fixture();self.assertIsNone(w._suspension_params_for(11))
    def test_contact_flagged_live(self):
        w,s,c=fixture();s['_contact_dynamics']=True;self.assertIsNone(w._suspension_params_for(11));self.assertTrue(s['_contact_dynamics'])
    def test_dead_wreck(self):
        w,s,c=fixture(False);self.assertIsNone(w._suspension_params_for(11))
    def test_explicit_detailed_probe_does_not_enable(self):
        w,s,c=fixture();w._suspension_ground_probe=w._wreck_ground_probe;self.assertIsNone(w._suspension_params_for(11))
    def test_cached_parameters_cannot_enable(self):
        w,s,c=fixture();w._suspension_params[11]={'synthetic':True};self.assertIsNone(w._suspension_params_for(11))
    def test_no_descriptor_needed_for_selection(self):
        w,s,c=fixture();w._descriptors={};self.assertIsNone(w._suspension_params_for(11));self.assertEqual(w._suspension_param_failures,0)
    def test_no_parameter_derivation(self):
        w,s,c=fixture();s['_contact_dynamics']=True;old=vp.derive_suspension_params;vp.derive_suspension_params=forbidden
        try:step(w,s)
        finally:vp.derive_suspension_params=old
    def test_no_spring_solver_for_stationary_flagged(self):
        w,s,c=fixture();s['_contact_dynamics']=True;old=vp.damper_suspension_step;vp.damper_suspension_step=forbidden
        try:
            for i in range(5):step(w,s)
        finally:vp.damper_suspension_step=old
        self.assertEqual(c,{'centre':5,'detailed':0});self.assertEqual(s['y'],0.)
    def test_no_spring_solver_for_moving(self):
        w,s,c=fixture();s.update(_contact_dynamics=True,speed=5.,movement_dir=1)
        w._update_suspension_vertical_motion=forbidden
        for i in range(5):s['z']+=1.;step(w,s)
        self.assertEqual(c,{'centre':5,'detailed':0});self.assertEqual(s['z'],5.);self.assertEqual(s['speed'],5.)
    def test_no_spring_solver_for_turning(self):
        w,s,c=fixture();s.update(_contact_dynamics=True,rotation_dir=1)
        w._update_suspension_vertical_motion=forbidden
        for i in range(5):s['yaw']+=.1;step(w,s)
        self.assertEqual(c['detailed'],0);self.assertAlmostEqual(s['yaw'],.5)
    def test_no_spring_solver_for_active_wreck(self):
        w,s,c=fixture(False);s.update(push_x=.5,push_yaw=.1)
        w._update_suspension_vertical_motion=forbidden;step(w,s)
        self.assertEqual(c['centre'],1);self.assertEqual(c['detailed'],0)
    def test_transition_to_wreck_stays_simple(self):
        w,s,c=fixture();s['_contact_dynamics']=True;step(w,s);s['alive']=False;step(w,s)
        self.assertEqual(c,{'centre':2,'detailed':0});self.assertEqual(w._suspension_params,{})
    def test_braking_contact_flag_no_activation(self):
        w,s,c=fixture();s['speed']=1.
        w._apply_tank_contact_response(s,dict(delta_velocity=(0.,-1.),correction=(0.,0.)),0.,advance_push=False,apply_correction=False)
        self.assertEqual(s['speed'],0.);self.assertTrue(s['_contact_dynamics']);step(w,s);self.assertEqual(c['detailed'],0)
    def test_state_reset_no_activation(self):
        w,s,c=fixture();s['_contact_dynamics']=True;w._reset_bot_suspension_state(s);step(w,s);self.assertEqual(c['detailed'],0)
    def test_low_mode_logged_once(self):
        w,s,c=fixture()
        for i in range(100):w._suspension_params_for(11)
        self.assertEqual(len(w._suspension_trial_reports),1)
    def test_dt_zero_keeps_basic_ground_check(self):
        w,s,c=fixture();s['_contact_dynamics']=True;step(w,s,0.);self.assertEqual(c,{'centre':1,'detailed':0})
    def test_streaming_no_initial_ground_not_fictitious_fall(self):
        w,s,c=fixture(ground=lambda *a:None);s.update(grounded_once=False,y=20.);step(w,s)
        self.assertEqual(s['y'],20.);self.assertFalse(s['airborne']);self.assertEqual(s['vertical_speed'],0.)
    def test_missing_ground_after_settle_still_falls(self):
        w,s,c=fixture(ground=lambda *a:None);s.update(_contact_dynamics=True,y=20.);step(w,s)
        self.assertTrue(s['airborne']);self.assertLess(s['vertical_speed'],0.);self.assertLess(s['y'],20.)
    def test_live_gravity_and_landing_still_run(self):
        w,s,c=fixture();s.update(_contact_dynamics=True,y=10.,airborne=True,vertical_speed=-2.)
        impacts=[];w._apply_bot_landing_impact=lambda st,speed,*a:impacts.append(speed)
        for i in range(40):step(w,s,.05)
        self.assertFalse(s['airborne']);self.assertEqual(s['y'],0.);self.assertEqual(len(impacts),1);self.assertLess(impacts[0],-2.)
    def test_wreck_gravity_and_landing_still_run(self):
        w,s,c=fixture(False);s.update(y=3.,airborne=True,vertical_speed=-2.)
        impacts=[];w._apply_bot_landing_impact=lambda st,speed,*a:impacts.append(speed)
        for i in range(30):step(w,s,.05)
        self.assertFalse(s['airborne']);self.assertEqual(s['y'],0.);self.assertEqual(len(impacts),1)
    def test_large_new_support_is_rejected(self):
        w,s,c=fixture(ground=lambda *a:4.);s['_contact_dynamics']=True
        self.assertTrue(step(w,s));self.assertEqual(s['y'],0.)
    def test_turret_guard_not_bypassed(self):
        w,s,c=fixture();calls=[]
        w._turret_motion_probe=lambda *a:(calls.append(a) or False)
        self.assertTrue(step(w,s));self.assertEqual(len(calls),1)
    def test_legacy_slope_pose_still_available(self):
        w,s,c=fixture(ground=lambda x,z,h:.1*z);self.assertTrue(w._update_slope_pose(s));self.assertNotEqual(s['pitch'],0.)
    def test_slope_pose_keeps_existing_movement_threshold(self):
        w,s,c=fixture(ground=lambda x,z,h:.1*z);w._update_slope_pose(s);before=dict(c)
        self.assertFalse(w._update_slope_pose(s));self.assertEqual(c,before)
    def test_mass_and_hitpoints_not_edited_by_selection(self):
        w,s,c=fixture();s.update(mass=130260.,health=493,_contact_dynamics=True);before=copy.deepcopy(s)
        w._suspension_params_for(11);self.assertEqual(s,before)
    def test_residual_angular_speed_does_not_reenable_solver(self):
        w,s,c=fixture(False);s.update(suspension_pitch_velocity=.2,suspension_roll_velocity=.1)
        w._update_suspension_vertical_motion=forbidden;step(w,s);self.assertEqual(c['detailed'],0)
    def test_many_bots_do_not_restore_full_solver(self):
        w,s,c=fixture();w._update_suspension_vertical_motion=forbidden
        for bid in range(1,30):
            v=copy.deepcopy(s);v.update(id=bid,_contact_dynamics=True,alive=bool(bid%2));w.states[bid]=v
            w._descriptors[bid]=descriptor();w._turn_speeds[bid]=0.;step(w,v)
        self.assertEqual(c['detailed'],0);self.assertEqual(c['centre'],29)
    def test_29_bodies_full_solver_is_zero_after_one_second(self):
        w,s,c=fixture();w._update_suspension_vertical_motion=forbidden
        rows=[]
        for bid in range(1,30):
            v=copy.deepcopy(s);v.update(id=bid,_contact_dynamics=True,alive=bool(bid%2));w.states[bid]=v
            w._descriptors[bid]=descriptor();w._turn_speeds[bid]=0.;rows.append(v)
        for i in range(5):
            for v in rows:step(w,v)
        self.assertEqual(c,{'centre':145,'detailed':0})

if __name__=='__main__':unittest.main(verbosity=2)
