from __future__ import print_function
import os,sys,math,unittest,types
W=os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0,W)
import test_097_low_support as low
br,vp=low.br,low.vp
pkg=types.ModuleType('gui.mods.offline_lan_0922.entities')
pkg.__path__=[os.path.join(low.PKG,'entities')]
sys.modules[pkg.__name__]=pkg
from gui.mods.offline_lan_0922.entities import remote_vehicle as rv

class BridgeTests(unittest.TestCase):
    def test_flat_bridge_never_launches_from_navigation_sentinel(self):
        for speed in (5.,12.,-6.):
            for pitch in (-math.atan(99.),math.atan(99.)):
                w,s,c=low.fixture();s.update(speed=speed,last_drive_pitch=pitch)
                for i in range(20):
                    s['z']+=speed*.1
                    low.step(w,s,.1)
                    self.assertAlmostEqual(s['y'],0.)
                    self.assertAlmostEqual(s['vertical_speed'],0.)
                    self.assertFalse(s['airborne'])
    def test_real_ahead_uphill_is_not_a_ramp_under_the_bridge(self):
        w,s,c=low.fixture();s.update(speed=10.,last_drive_pitch=-.4)
        for i in range(10):low.step(w,s,.1)
        self.assertEqual(s['y'],0.);self.assertEqual(s['vertical_speed'],0.)
    def test_missing_ground_starts_falling_from_level_deck(self):
        w,s,c=low.fixture(ground=lambda *a:None)
        s.update(y=8.,speed=10.,last_drive_pitch=-math.atan(99.))
        low.step(w,s,.1)
        self.assertLess(s['y'],8.);self.assertLess(s['vertical_speed'],0.)
    def test_visible_valley_floor_does_not_create_upward_kick(self):
        w,s,c=low.fixture(ground=lambda *a:-10.)
        s.update(speed=10.,last_drive_pitch=-math.atan(99.))
        low.step(w,s,.1)
        self.assertLess(s['y'],0.);self.assertGreater(s['y'],-10.)
        self.assertTrue(s['airborne'])
    def test_reverse_off_bridge_falls(self):
        w,s,c=low.fixture(ground=lambda *a:None)
        s.update(speed=-6.,last_drive_pitch=math.atan(99.))
        low.step(w,s,.1);self.assertLess(s['vertical_speed'],0.)
    def test_real_ramp_retains_measured_upward_momentum(self):
        w,s,c=low.fixture(ground=lambda x,z,h:.2*z if z<2. else None)
        s.update(z=1.,speed=10.,last_drive_pitch=-.2)
        low.step(w,s,.1)
        self.assertAlmostEqual(s['y'],.2);self.assertAlmostEqual(s['vertical_speed'],2.)
        s['z']=2.;low.step(w,s,.1)
        self.assertTrue(s['airborne']);self.assertGreater(s['y'],.2)
        self.assertAlmostEqual(s['vertical_speed'],2.-vp.GRAVITY*.1)
    def test_existing_airborne_momentum_is_not_deleted(self):
        w,s,c=low.fixture(ground=lambda *a:None)
        s.update(y=8.,airborne=True,vertical_speed=3.,last_drive_pitch=-1.56)
        low.step(w,s,.1)
        self.assertAlmostEqual(s['vertical_speed'],3.-vp.GRAVITY*.1)
    def test_zero_dt_does_not_divide_or_launch(self):
        w,s,c=low.fixture();s.update(speed=10.,last_drive_pitch=-1.56)
        low.step(w,s,0.);self.assertEqual(s['vertical_speed'],0.)
    def test_grounded_wreck_cannot_launch_from_stale_drive_pitch(self):
        w,s,c=low.fixture(False);s.update(speed=5.,last_drive_pitch=-1.56)
        for i in range(4):low.step(w,s,.1)
        self.assertEqual(s['y'],0.);self.assertFalse(s['airborne'])
    def test_invalid_grades_use_accepted_support_pose(self):
        for probe in (None,{},dict(slope=99),dict(slope=-99),dict(slope=float('nan')),
                      dict(slope=float('inf')),dict(slope=.3,slope_valid=False),
                      dict(slope=.3,probe_failed=True)):
            self.assertAlmostEqual(br._motion_drive_pitch(probe,-1.,dict(terrain_pitch=.12)),.12)
    def test_valid_forward_and_reverse_grades_keep_sign(self):
        for grade in (-.3,0.,.3):
            for sign in (-1.,1.):
                self.assertAlmostEqual(br._motion_drive_pitch(dict(slope=grade),sign,{}),-sign*math.atan(grade))
    def test_fallback_does_not_include_recoil_pitch(self):
        self.assertAlmostEqual(br._motion_drive_pitch({},1.,dict(pitch=.3,suspension_pitch=.2)),.1)

class Timeline(object):
    def __init__(self):self.playing=True;self.pending=True;self.attached=True;self.stops=0
    def stop(self):self.playing=False;self.pending=False;self.attached=False;self.stops+=1
class Bound(object):
    def __init__(self):self._effects=[Timeline(),Timeline()];self.stops=0
    def stop(self):
        self.stops+=1
        for effect in self._effects[:]:effect.stop();self._effects.remove(effect)
class Selector(object):
    def __init__(self):self._enabled=True
    def stop(self):self._enabled=False
class VisualTests(unittest.TestCase):
    def fixture(self):
        a=low.Obj(id=17,compoundModel=object(),boundEffects=Bound(),engineAudition=object(),detailedEngineState=object())
        a.customEffectManager=low.Obj(_CustomEffectManager__selectors=[Selector(),Selector()])
        a._CompoundAppearance__effectsPlayer=Timeline()
        def stop():
            a._CompoundAppearance__effectsPlayer.stop()
            a._CompoundAppearance__effectsPlayer=None
        a._CompoundAppearance__stopEffects=stop
        return a
    def test_hide_closes_independent_timelines_and_emitters(self):
        a=self.fixture();effects=a.boundEffects._effects[:]+[a._CompoundAppearance__effectsPlayer]
        rv.close_stock_presentation_extras(a,False)
        self.assertTrue(all(not e.playing and not e.pending and not e.attached for e in effects))
        self.assertTrue(all(not s._enabled for s in a.customEffectManager._CustomEffectManager__selectors))
    def test_visible_vehicle_effects_are_untouched(self):
        a=self.fixture();rv.close_stock_presentation_extras(a,True)
        self.assertTrue(a._CompoundAppearance__effectsPlayer.playing)
        self.assertEqual(len(a.boundEffects._effects),2)
    def test_repeated_hide_is_idempotent(self):
        a=self.fixture();rv.close_stock_presentation_extras(a,False)
        for i in range(20):rv.stop_bound_visual_effects(a)
        self.assertEqual(a.boundEffects.stops,1)
    def test_new_hidden_timeline_is_closed_by_periodic_gate(self):
        a=self.fixture();rv.close_stock_presentation_extras(a,False)
        late=Timeline();a.boundEffects._effects.append(late)
        rv.stop_bound_visual_effects(a)
        self.assertFalse(late.attached);self.assertFalse(late.pending)
    def test_one_owner_failure_still_closes_other_owner(self):
        a=self.fixture()
        def fail():raise RuntimeError('synthetic stop failure')
        a._CompoundAppearance__stopEffects=fail
        rv.stop_bound_visual_effects(a)
        self.assertEqual(a.boundEffects._effects,[])
    def test_audio_owners_are_never_changed(self):
        a=self.fixture();audio=a.engineAudition;state=a.detailedEngineState
        rv.close_stock_presentation_extras(a,False)
        self.assertIs(a.engineAudition,audio);self.assertIs(a.detailedEngineState,state)
    def test_retired_appearance_is_safe(self):
        self.assertFalse(rv.stop_bound_visual_effects(None))
        rv.stop_bound_visual_effects(low.Obj(compoundModel=None))

class NativeBoundaryTests(unittest.TestCase):
    def run_edge(self,visible):
        from gui.mods.offline_lan_0922.entities import native_remote_vehicle as nv
        events=[]
        a=low.Obj(compoundModel=object(),changeVisibility=lambda v:events.append(('draw',v)))
        e=low.Obj(appearance=a,show=lambda v:events.append(('show',v)))
        names=('set_engine_audible','close_stock_presentation_extras','set_model_attachment_visibility')
        old=[getattr(nv,n) for n in names]
        try:
            nv.set_engine_audible=lambda e,v:events.append(('audio',v))
            nv.close_stock_presentation_extras=lambda a,v:events.append(('effects',v))
            nv.set_model_attachment_visibility=lambda m,v:events.append(('attachments',v))
            nv.set_draw_visibility(e,visible)
        finally:
            for n,f in zip(names,old):setattr(nv,n,f)
        return events
    def test_hide_stops_timelines_before_r11_audio_retirement(self):
        self.assertEqual(self.run_edge(False),[('effects',False),('audio',False),('show',False),('draw',False),('attachments',False)])
    def test_reveal_keeps_r11_audio_restore_and_visual_restore(self):
        self.assertEqual(self.run_edge(True),[('audio',True),('show',True),('draw',True),('attachments',True),('effects',True)])

if __name__=='__main__':unittest.main(verbosity=2)
