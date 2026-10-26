"""Compare the native actor phase with the current mod source, using explicit query fixtures."""
import ast,ctypes,math,os,random,sys,types
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'src/res/scripts/client'))
from gui.mods.offline_lan_0922 import vehicle_physics as v,tank_collision as tc
path=str(ROOT/'src/res/scripts/client/gui/mods/offline_lan_0922/battle_runtime.py')
tree=ast.parse(open(path).read());names=['_update_suspension_vertical_motion','_local_suspension_rise_is_continuous','_local_suspension_path_is_supported','_commit_local_suspension_metadata','_apply_landing_impact','_apply_fall_damage'];methods=[n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name in names]
cls=ast.ClassDef(name='Source',bases=[],keywords=[],body=methods,decorator_list=[]);ns={'math':math,'vehicle_physics':v,'tank_collision':tc,'GROUND_PLANE_EPSILON':.35,'MAX_PENDING_LANDING_IMPACTS':32,'lan_protocol':types.SimpleNamespace(PLAYER_LANDING_MAX_IMPACT_SPEED=200)}
exec(compile(ast.fix_missing_locations(ast.Module(body=[cls],type_ignores=[])),path,'exec'),ns);Source=ns['Source']
lib=ctypes.CDLL(os.environ['WOT_AUTHORITY_PROBE']);lib.ground.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p]
p={'mass':10000,'pitch_inertia':30000,'roll_inertia':10000,'fixed_step':1/60,'constraint_iterations':10,'springs':[],'pseudo_contacts':[]}
for pair in range(5):
 for side in range(2):p['springs'].append({'x':1 if side else -1,'y':0,'z':pair-2,'side':'right' if side else 'left','static_compression':.2,'max_compression':.4,'stiffness':10000*9.81*1.25/(10*.2),'damping':2000,'max_force':100000})
rng=random.Random(1513);maximum=0
for case in range(1000):
 x=[rng.uniform(-.5,.5),rng.uniform(-.2,1),rng.uniform(-.5,.5),rng.uniform(-math.pi,math.pi),rng.uniform(-5,10),rng.uniform(-1,1),rng.uniform(-.15,.15),rng.uniform(-.15,.15),rng.uniform(-15,4),rng.uniform(-.2,.2),rng.uniform(-.2,.2),rng.uniform(0,.2),0,0,0];flags=case%8;mask=rng.randrange(1024)
 if case%11==0:mask=0
 if case%13==0:mask=1023
 raw=[rng.uniform(-.3,1.2) for _ in range(10)];samples=[raw[i] if mask&(1<<i) else None for i in range(10)]
 a=Source();a._local_suspension_params=p;a._local_fall_armed=bool(flags&1);a._local_airborne=bool(flags&2);a._local_vertical_speed=x[8];a._local_pitch=x[6];a._local_roll=x[7];a._local_suspension_pitch_velocity=x[9];a._local_suspension_roll_velocity=x[10];a._local_suspension_support_vertical_speed=0;a._local_suspension_support_gradient=None;a._local_support_tick_pose=tuple(x[12:]);a._local_support_motion_pose=tuple(x[12:]);a._local_ground_plane={'center_x':0,'center_z':0,'center_y':0,'gradient_x':0,'gradient_z':0,'normal':(0,1,0)} if flags&4 else None;a._local_speed=x[4];a._local_turn_speed=x[5];a._local_drive_turn=0;a._local_air_lateral=(3,4);a._local_slide_speed=0;a._pending_landing_impacts=[];a._local_frame_stages=None;a._local_left_flying=False;a._local_right_flying=False;a._ground_y=lambda *args,**kw:None;a._local_suspension_predicted_probe_height=lambda pos,*args:pos[1];a._local_suspension_ground_samples=lambda *args,**kw:samples;a._local_suspension_pseudo_ground_samples=lambda *args,**kw:[];a._prepared_ground_filter=lambda *args:None;a._suspension_ground_y=lambda *args,**kw:None
 entity=types.SimpleNamespace(typeDescriptor=types.SimpleNamespace(maxHealth=400),health=400)
 pos=a._update_suspension_vertical_motion(entity,tuple(x[:3]),x[3],x[11])
 expected=[pos[1],a._local_vertical_speed,a._local_pitch,a._local_suspension_pitch_velocity,a._local_roll,a._local_suspension_roll_velocity,a._local_suspension_support_vertical_speed,float(a._local_airborne),float(a._local_left_flying),float(a._local_right_flying),float(a._local_fall_armed),pos[0],pos[2],a._local_speed,a._local_turn_speed,float(a._local_support_rise_blocked),len(a._pending_landing_impacts)]
 inputs=(ctypes.c_double*15)(*x);heights=(ctypes.c_double*10)(*raw);out=(ctypes.c_double*17)();lib.ground(inputs,heights,mask,flags,out)
 for index,(actual,reference) in enumerate(zip(out,expected)):
  error=abs(actual-reference)/max(1,abs(reference));maximum=max(maximum,error)
  if error>2e-10:raise AssertionError((case,index,actual,reference,flags,mask,x))
print('human suspension acceptance source: 1000 cases passed; maximum scaled error %.3g'%maximum)
