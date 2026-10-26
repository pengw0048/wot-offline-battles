"""Compare the native actor phase with the current mod source, using explicit query fixtures."""
import ast,ctypes,math,os,random,sys,types
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'src/res/scripts/client'))
from gui.mods.offline_lan_0922 import vehicle_physics as v
path=str(ROOT/'src/res/scripts/client/gui/mods/offline_lan_0922/battle_runtime.py')
tree=ast.parse(open(path).read());methods=[]
for node in ast.walk(tree):
 if isinstance(node,ast.FunctionDef) and node.name in ['_drive_local_step','_local_autorotation_turn','_smoothed_drive_pitch']:
  if node.name=='_drive_local_step':
   kept=[]
   for stmt in node.body:
    if isinstance(stmt,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='suspension_active' for t in stmt.targets):break
    kept.append(stmt)
   kept.append(ast.parse("self._prefix_result = (position[0],position[1],position[2],yaw,self._local_speed,self._local_turn_speed,self._local_grind,self._local_drive_turn)").body[0]);node.body=kept
  methods.append(node)
cls=ast.ClassDef(name='Source',bases=[],keywords=[],body=methods,decorator_list=[])
module=ast.fix_missing_locations(ast.Module(body=[cls],type_ignores=[]))
def field(obj,key,default=None):return obj.get(key,default) if isinstance(obj,dict) else getattr(obj,key,default)
ns={'math':math,'vehicle_physics':v,'_number':lambda x,default=0:float(x),'_field':field,'_angle_delta':lambda a,b:(a-b+math.pi)%(2*math.pi)-math.pi,'GUN_TRAVERSE_LIMIT_EPSILON':1e-5,'critical_damage':types.SimpleNamespace(stat_factor=lambda entity,key:getattr(entity,key+'_factor'))}
exec(compile(module,path,'exec'),ns);Source=ns['Source']
lib=ctypes.CDLL(os.environ['WOT_AUTHORITY_PROBE']);lib.human.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_uint,ctypes.c_int,ctypes.c_void_p]
rng=random.Random(1513);N=3000;error=0
for case in range(N):
 p=[rng.uniform(5000,100000),rng.uniform(50000,1000000),rng.uniform(5,25),rng.uniform(2,10),rng.uniform(.1,1.5),1,1.2,1.8,rng.uniform(.5,1.5),rng.uniform(1,15),rng.uniform(1,2),rng.uniform(.5,1.5)]
 x=[rng.uniform(-100,100),10,rng.uniform(-100,100),rng.uniform(-math.pi,math.pi),rng.uniform(-8,20),rng.uniform(-1,1),rng.randrange(5),rng.choice([-1,0,1]),rng.choice([-1,0,1]),rng.uniform(.001,.1),rng.uniform(-.8,.8),0,rng.uniform(.3,1),rng.uniform(.3,1),rng.uniform(.5,1.5),rng.uniform(-6,6)]
 flags=case%256;status=case%5
 a=Source();a._sender=types.SimpleNamespace(forward=x[7],turn=x[8],handbrake=bool(flags&2),aim_yaw=x[15]);a._server=types.SimpleNamespace(vehicle_id=1)
 entity=types.SimpleNamespace(typeDescriptor={'gun':{'turretYawLimits':(-.4,.7) if flags&64 else None}},isAlive=lambda:True,is_tracked=bool(flags&16),is_engine_dead=bool(flags&32),mobility_factor=x[12],traverse_factor=x[13],filter=None)
 a._server_entity=lambda identity:entity;a._battle_result=None;a._local_physics=dict(zip(['mass','powerW','speedFwd','speedBwd','rotSpd'],p[:5]),terrainResist=tuple(p[5:8]),specificFriction=p[8],brakeDecel=p[9],trackCenter=p[10],nativePowerRatio=p[11]);a._local_position=tuple(x[:3]);a._local_yaw=x[3];a._local_speed=x[4];a._local_turn_speed=x[5];a._local_grind=int(x[6]);a._local_pitch=a._local_roll=0;a._local_airborne=bool(flags&1);a._overturn_level=2 if flags&8 else 0;a._detached_turret_obstacles=None;a._destructibles=None;a._local_drive_pitch_history=None;a._local_smooth_drive_pitch=0;a._drive_pitch=lambda position,yaw:x[10];a._local_siege_drive_locked=lambda entity:bool(flags&4);a._active_engine_power_factor=lambda:x[14];a._avatar=types.SimpleNamespace(inputHandler=types.SimpleNamespace(getAutorotation=lambda:bool(flags&64)));a._arena_rotation_is_clear=lambda *args:True;a._pose_sweep_is_clear=lambda *args:True;a._report_destructible_verdict=lambda *args,**kw:None;a._local_motion_status='clear';a._local_motion_cap_crushed=False;a._local_motion_soft_block=False
 def motion(*args,**kw):
  current=status if kw.get('hull_yaw') is None else (0 if flags&128 else 4)
  a._local_motion_status=['clear','crushed','soft','cap_crushed','hard'][current];a._local_motion_cap_crushed=current==3;a._local_motion_soft_block=current==2
  return current in (0,1)
 a._motion_is_clear=motion
 a._drive_local_step(x[9])
 # Position/yaw are local variables until the method's final commit; add a
 # source AST return immediately before the excluded vertical phase below.
 # Read them via the trace capture installed in a generated final Return.
 params=(ctypes.c_double*12)(*p);inputs=(ctypes.c_double*16)(*x);out=(ctypes.c_double*8)();lib.human(params,inputs,flags,status,out)
 # return values populated by the source prefix at the tail.
 expected=a._prefix_result
 for i,(actual,reference) in enumerate(zip(out,expected)):
  delta=abs(actual-reference)/max(1,abs(reference));error=max(error,delta)
  if delta>2e-11:raise AssertionError((case,i,actual,reference,flags,status,x))
print('human authoritative source prefix: %d cases passed; max scaled error %.3g'%(N,error))
