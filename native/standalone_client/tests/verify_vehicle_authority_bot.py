"""Compare the native actor phase with the current mod source, using explicit query fixtures."""
import ast,ctypes,math,os,random,sys,types
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'src/res/scripts/client'))
from gui.mods.offline_lan_0922 import vehicle_physics as v
path=str(ROOT/'src/res/scripts/client/gui/mods/offline_lan_0922/bot_runtime.py')
tree=ast.parse(open(path).read());methods=[];body=None
for node in ast.walk(tree):
 if isinstance(node,ast.FunctionDef) and node.name in ['_hard_contact_response','_passive_motion_status']:methods.append(node)
 if isinstance(node,ast.FunctionDef) and node.name=='_update_once':
  candidates=[n for n in ast.walk(node) if isinstance(n,ast.If) and isinstance(n.test,ast.UnaryOp) and isinstance(n.test.op,ast.Not) and isinstance(n.test.operand,ast.Attribute) and n.test.operand.attr=='native_motion']
  assert len(candidates)==1, 'Expected exactly one authoritative horizontal source block'
  body=candidates[0].body
assert body
cls=ast.ClassDef(name='Source',bases=[],keywords=[],body=methods,decorator_list=[])
for m in methods:m.decorator_list=[]
position=lambda s:(s['x'],s['y'],s['z'])
number=lambda v,d=0:float(v if v is not None else d)
timed=lambda diagnostics,name,method,*args,**kwargs:method(*args,**kwargs)
ns={'math':math,'vehicle_physics':v,'_number':number,'_position':position,'_angle_delta':lambda a,b:(a-b+math.pi)%(2*math.pi)-math.pi,'timed_call':timed,'siege_mechanics':types.SimpleNamespace(ENABLED=2,enabled_speed_limit=lambda vehicle:None),'BOT_WATER_AVOID_DEPTH':.9}
exec(compile(ast.fix_missing_locations(ast.Module(body=[cls],type_ignores=[])),path,'exec'),ns)
code=compile(ast.fix_missing_locations(ast.Module(body=body,type_ignores=[])),path,'exec');Source=ns['Source']
lib=ctypes.CDLL(os.environ['WOT_AUTHORITY_PROBE']);lib.bot.argtypes=[ctypes.c_void_p,ctypes.c_void_p,ctypes.c_uint,ctypes.c_int,ctypes.c_void_p]
rng=random.Random(1513);error=0
for case in range(3000):
 p=[rng.uniform(5000,100000),rng.uniform(50000,1000000),rng.uniform(5,25),rng.uniform(2,10),rng.uniform(.1,1.5),1,1.2,1.8,rng.uniform(.5,1.5),rng.uniform(1,15),rng.uniform(1,2),rng.uniform(.5,1.5)]
 x=[rng.uniform(-100,100),10,rng.uniform(-100,100),rng.uniform(-math.pi,math.pi),rng.uniform(-8,20),rng.uniform(-1,1),rng.randrange(5),rng.choice([-1,0,1]),rng.choice([-1,0,1]),rng.uniform(.001,.2),rng.uniform(-.8,.8),0,rng.uniform(.3,1),rng.uniform(.3,1),rng.uniform(.5,1.5),rng.uniform(-6,6)]
 flags=case%256;status=case%5;physics=dict(zip(['mass','powerW','speedFwd','speedBwd','rotSpd'],p[:5]),terrainResist=tuple(p[5:8]),specificFriction=p[8],brakeDecel=p[9],trackCenter=p[10],nativePowerRatio=p[11]);state={'id':1,'x':x[0],'y':x[1],'z':x[2],'yaw':x[3],'speed':x[4],'airborne':bool(flags&1),'rotation_dir':0,'movement_dir':0}
 a=Source();a._turn_speeds={1:x[5]};a._hard_contact_grinds={1:int(x[6])};a._physics_params_for=lambda id:physics;a._baked_pose_progress_clear=lambda *args:True;a._turret_pose_is_clear=lambda *args:True;a._planner_corridor_clear=lambda *args,**kw:True;a._probe_timing_enabled=lambda:False;a._combat_diagnostics=None;a.navigator=None;a.motion_report=None;a._invalidate_realised_motion=lambda *args:None
 def motion(id,pos,yaw,speed,descriptor,step,now,commit_enabled=True,motion_yaw=None):return ['clear','crushed','soft','cap_crushed','hard'][status if motion_yaw is None else (0 if flags&128 else 4)]
 a.motion_resolver=motion
 throttle=max(-1,min(1,x[7]));turn=max(-1,min(1,x[8]));siege=bool(flags&4)
 if siege:throttle=turn=0;state['speed']=0;a._turn_speeds[1]=0
 if flags&(8|16|32):throttle=turn=0
 elif abs(throttle)>.01:throttle*=x[12]
 travel_sign=-1 if throttle<0 or abs(throttle)<=.01 and state['speed']<0 else 1
 steer_dir=(1 if turn>0 else -1) if abs(turn)>.01 else 0;state['rotation_dir']=steer_dir;state['movement_dir']=1 if throttle>.01 else -1 if throttle<-.01 else 0
 loc=dict(self=a,state=state,position=position(state),throttle=throttle,turn=turn,step=x[9],now=x[9],steer_dir=steer_dir,siege_motion_locked=siege,motion_probe={'slope':math.tan(-x[10]/travel_sign)},travel_sign=travel_sign,travel_yaw=state['yaw'] if travel_sign>0 else state['yaw']+math.pi,attempted_yaws={},baked_shallow_escape=False,controlled_shallow=None,controlled_shallow_commit=None,path_clear=True,pose_frozen=False,exact_motion_owns_collision=True,descriptor={},command={'move_position':None},navigation_grid=None,diagnostic=None)
 exec(code,ns,loc)
 params=(ctypes.c_double*12)(*p);inputs=(ctypes.c_double*16)(*x);out=(ctypes.c_double*8)();lib.bot(params,inputs,flags,status,out)
 expected=[state['x'],state['y'],state['z'],state['yaw'],state['speed'],a._turn_speeds[1],a._hard_contact_grinds.get(1,0)]
 for i,(actual,reference) in enumerate(zip(out,expected)):
  delta=abs(actual-reference)/max(1,abs(reference));error=max(error,delta)
  if delta>2e-11:raise AssertionError((case,i,actual,reference,flags,status,x))
print('bot authoritative source block: 3000 cases passed; max scaled error %.3g'%error)
