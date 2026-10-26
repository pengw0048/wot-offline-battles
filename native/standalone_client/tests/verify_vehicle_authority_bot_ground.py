"""Compare the native actor phase with the current mod source, using explicit query fixtures."""
import ast,ctypes,math,os,random,sys,types
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'src/res/scripts/client'))
from gui.mods.offline_lan_0922 import vehicle_physics as v
path=str(ROOT/'src/res/scripts/client/gui/mods/offline_lan_0922/bot_runtime.py');tree=ast.parse(open(path).read())
names=['_update_suspension_vertical_motion','_snapshot_bot_suspension_state','_restore_bot_suspension_state','_reset_bot_suspension_state','_suspension_world_ground_plane','_suspension_plane_height','_suspension_probe_height_for_motion','_suspension_rise_exceeds_base','_suspension_rise_has_continuous_support','_suspension_path_supports_plane','_apply_bot_landing_impact','_apply_bot_fall_damage']
methods=[];fields=None
for node in ast.walk(tree):
 if isinstance(node,ast.FunctionDef) and node.name in names:
  node.decorator_list=[d for d in node.decorator_list if isinstance(d,ast.Name) and d.id=='staticmethod'];methods.append(node)
 if isinstance(node,ast.Assign) and any(isinstance(n,ast.Name) and n.id=='_BOT_SUSPENSION_STATE_FIELDS' for n in node.targets):fields=ast.literal_eval(node.value)
ns={'math':math,'vehicle_physics':v,'_number':lambda v,d=0:float(v if v is not None else d),'_position':lambda s:(s['x'],s['y'],s['z']),'_BOT_SUSPENSION_STATE_FIELDS':fields,'SUSPENSION_BASE_RISE':.6,'SUSPENSION_GROUND_PLANE_EPSILON':.35,'SUSPENSION_GROUND_GRADIENT_EPSILON':.35}
cls=ast.ClassDef(name='Source',bases=[],keywords=[],body=methods,decorator_list=[]);exec(compile(ast.fix_missing_locations(ast.Module(body=[cls],type_ignores=[])),path,'exec'),ns);Source=ns['Source']
lib=ctypes.CDLL(os.environ['WOT_AUTHORITY_PROBE']);lib.bot_ground.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p]
p={'mass':10000,'pitch_inertia':30000,'roll_inertia':10000,'fixed_step':1/60,'constraint_iterations':10,'springs':[],'pseudo_contacts':[]}
for pair in range(5):
 for side in range(2):p['springs'].append({'x':1 if side else -1,'y':0,'z':pair-2,'side':'right' if side else 'left','static_compression':.2,'max_compression':.4,'stiffness':10000*9.81*1.25/(10*.2),'damping':2000,'max_force':100000})
rng=random.Random(1513);maximum=0
for case in range(1000):
 x=[rng.uniform(-.5,.5),rng.uniform(-.2,1),rng.uniform(-.5,.5),rng.uniform(-math.pi,math.pi),rng.uniform(-5,10),rng.uniform(-1,1),rng.uniform(-.15,.15),rng.uniform(-.15,.15),rng.uniform(-15,4),rng.uniform(-.2,.2),rng.uniform(-.2,.2),rng.uniform(0,.2),0,0,0];flags=case%16;mask=rng.randrange(1024)
 if case%11==0:mask=0
 if case%13==0:mask=1023
 if flags&8:flags|=1
 raw=[rng.uniform(-.3,1.2) for _ in range(10)];samples=[raw[i] if mask&(1<<i) else None for i in range(10)]
 s={'id':1,'x':x[0],'y':x[1],'z':x[2],'yaw':x[3],'speed':x[4],'pitch':x[6],'terrain_pitch':x[6],'roll':x[7],'vertical_speed':x[8],'suspension_pitch_velocity':x[9],'suspension_roll_velocity':x[10],'grounded_once':bool(flags&1),'airborne':bool(flags&2),'air_lateral_x':3,'air_lateral_z':4,'slide_speed':0,'left_flying':False,'right_flying':False,'_suspension_support_vertical_speed':0,'health':400,'max_health':400,'alive':True}
 if flags&4:s['_suspension_ground_plane']={'center_x':0,'center_z':0,'center_y':0,'gradient_x':0,'gradient_z':0,'normal':(0,1,0)}
 a=Source();a._turn_speeds={1:x[5]};a._turret_pending_landing_impacts=None;a._probe_totals=[0]*6;a._probe_started=lambda:0;a._probe_finished=lambda *args:None;a._physics_ground_probe=lambda *args:3 if flags&8 else None;a._suspension_ground_samples=lambda *args:samples;a._suspension_pseudo_ground_samples=lambda *args:[];a._suspension_ground_value=lambda *args:None;a._hidden_suspension_support_is_raised=lambda state:bool(flags&8);a._invalidate_realised_motion=lambda *args:None
 blocked=a._update_suspension_vertical_motion(s,x[11],p,tuple(x[12:]),x[3])
 expected=[s['y'],s['vertical_speed'],s['pitch'],s['suspension_pitch_velocity'],s['roll'],s['suspension_roll_velocity'],s.get('_suspension_support_vertical_speed',0),float(s['airborne']),float(s['left_flying']),float(s['right_flying']),float(s['grounded_once']),s['x'],s['z'],s['speed'],a._turn_speeds[1],float(blocked),0]
 inputs=(ctypes.c_double*15)(*x);heights=(ctypes.c_double*10)(*raw);out=(ctypes.c_double*17)();lib.bot_ground(inputs,heights,mask,flags,out)
 for index,(actual,reference) in enumerate(zip(out,expected)):
  error=abs(actual-reference)/max(1,abs(reference));maximum=max(maximum,error)
  if error>2e-10:raise AssertionError((case,index,actual,reference,flags,mask,x))
print('bot suspension acceptance source: 1000 cases passed; maximum scaled error %.3g'%maximum)
