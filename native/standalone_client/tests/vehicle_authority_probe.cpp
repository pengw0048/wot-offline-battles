#ifdef _WIN32
#define WOT_PROBE_EXPORT __declspec(dllexport)
#else
#define WOT_PROBE_EXPORT
#endif
// Current-mod differential probe. Query fixtures are explicit; this is not a scene adapter.
#include "wot/vehicle_authority.hpp"
#include <cmath>
using namespace wot::offline_vehicle_control;
struct S:AuthorityServices {
 double pitch;int primary;bool glance;
 double human_drive_pitch(const AuthorityActor&)override{return pitch;}
 AuthorityMotionStatus motion(const AuthorityActor&,const AuthorityMotionQuery&q)override{
  double angle=q.travel_yaw-(q.speed<0?3.14159265358979323846:0);
  return std::abs(angle-q.hull_yaw)<1e-8?static_cast<AuthorityMotionStatus>(primary):(glance?AuthorityMotionStatus::clear:AuthorityMotionStatus::hard);
 }
 AuthorityRotationStatus rotation(const AuthorityActor&,const AuthorityPose&,double)override{return AuthorityRotationStatus::clear;}
 AuthorityBotAdmission bot_admission(const AuthorityActor&,double,double,bool)override{return{true,false,false,true,pitch};}
 void prepare_bot(AuthorityActor&,double,double,bool)override{}
 void aim_and_fire(AuthorityActor&,double,std::int64_t,std::int64_t)override{}
 void ground_step(AuthorityActor&,const AuthorityPose&,double)override{}
 void tank_contacts(std::vector<AuthorityActor*>&,double,double)override{}
 void finish_pose(AuthorityActor&,const AuthorityMotionState&,const AuthorityMotionState&,double)override{}
 void invalidate_motion(AuthorityActor&,double)override{}
 std::optional<double>next_burst_time_left()const override{return{};}
 bool has_pending_barrier()const override{return false;}
 bool publish(const std::vector<AuthorityActor*>&,std::int64_t)override{return true;}
};
extern "C" WOT_PROBE_EXPORT void human(const double*p,const double*x,unsigned flags,int status,double*out){
 AuthorityActor a{};a.id=1;a.kind=AuthorityActorKind::human;
 a.parameters={p[0],p[1],p[2],p[3],p[4],{p[5],p[6],p[7]},p[8],p[9],p[10],p[11]};
 a.state.pose.position={x[0],x[1],x[2]};a.state.pose.yaw=x[3];a.state.speed=x[4];a.state.turn_speed=x[5];a.state.grind_ticks=(int)x[6];a.state.airborne=flags&1;
 a.command.throttle=x[7];a.command.turn=x[8];a.command.handbrake=flags&2;a.command.autorotation_enabled=flags&64;a.command.aim_yaw=x[15];
 if(flags&64)a.command.turret_yaw_limits=std::array<double,2>{-.4,.7};
 a.condition.siege_switching=flags&4;a.condition.overturned=flags&8;a.condition.track_destroyed=flags&16;a.condition.engine_destroyed=flags&32;
 a.condition.mobility_factor=x[12];a.condition.traverse_factor=x[13];a.condition.engine_power_factor=x[14];
 S s;s.pitch=x[10];s.primary=status;s.glance=flags&128;
 advance_authority_drivetrain(a,x[9],x[9],true,s);
 const auto&b=a.state;out[0]=b.pose.position[0];out[1]=b.pose.position[1];out[2]=b.pose.position[2];out[3]=b.pose.yaw;out[4]=b.speed;out[5]=b.turn_speed;out[6]=b.grind_ticks;out[7]=b.drive_turn;
}
extern "C" WOT_PROBE_EXPORT void bot(const double*p,const double*x,unsigned flags,int status,double*out){
 AuthorityActor a{};a.id=1;a.kind=AuthorityActorKind::bot;
 a.parameters={p[0],p[1],p[2],p[3],p[4],{p[5],p[6],p[7]},p[8],p[9],p[10],p[11]};
 a.state.pose.position={x[0],x[1],x[2]};a.state.pose.yaw=x[3];a.state.speed=x[4];a.state.turn_speed=x[5];a.state.grind_ticks=(int)x[6];a.state.airborne=flags&1;
 a.command.throttle=x[7];a.command.turn=x[8];a.command.handbrake=flags&2;a.command.autorotation_enabled=flags&64;a.command.aim_yaw=x[15];
 if(flags&64)a.command.turret_yaw_limits=std::array<double,2>{-.4,.7};
 a.condition.siege_switching=flags&4;a.condition.overturned=flags&8;a.condition.track_destroyed=flags&16;a.condition.engine_destroyed=flags&32;
 a.condition.mobility_factor=x[12];a.condition.traverse_factor=x[13];a.condition.engine_power_factor=x[14];
 S s;s.pitch=x[10];s.primary=status;s.glance=flags&128;
 advance_authority_drivetrain(a,x[9],x[9],true,s);
 const auto&b=a.state;out[0]=b.pose.position[0];out[1]=b.pose.position[1];out[2]=b.pose.position[2];out[3]=b.pose.yaw;out[4]=b.speed;out[5]=b.turn_speed;out[6]=b.grind_ticks;out[7]=b.drive_turn;
}
extern "C" WOT_PROBE_EXPORT void ground(const double*x,const double*heights,unsigned mask,unsigned flags,double*out){
 AuthorityActor a{};a.id=1;a.kind=AuthorityActorKind::human;a.condition.maximum_health=400;
 SuspensionParameters p;p.mass=10000;p.pitch_inertia=30000;p.roll_inertia=10000;p.fixed_step=1./60;p.constraint_iterations=10;
 for(int pair=0;pair<5;++pair)for(int side=0;side<2;++side){SuspensionSpring s;s.x=side?1:-1;s.z=pair-2;s.side=side?ContactSide::right:ContactSide::left;s.static_compression=.2;s.max_compression=.4;s.stiffness=p.mass*9.81*1.25/(10*.2);s.damping=2000;s.max_force=100000;p.springs.push_back(s);}
 a.suspension=p;a.state.pose.position={x[0],x[1],x[2]};a.state.pose.yaw=x[3];a.state.speed=x[4];a.state.turn_speed=x[5];a.state.pose.pitch=x[6];a.state.pose.roll=x[7];a.state.vertical_speed=x[8];a.state.pitch_velocity=x[9];a.state.roll_velocity=x[10];a.state.grounded_once=flags&1;a.state.airborne=flags&2;a.state.air_lateral={3,4};
 if(flags&4){GroundPlane old;old.center_x=0;old.center_z=0;old.normal={0,1,0};a.state.ground_plane=old;}
 AuthorityPose before;before.position={x[12],x[13],x[14]};AuthorityGroundSnapshot q;q.motion_pose=before;for(unsigned i=0;i<10;++i)q.springs.push_back(mask&(1<<i)?std::optional<double>(heights[i]):std::nullopt);
 auto result=settle_authority_suspension(a,before,q,x[11],{});
 const auto&s=a.state;double values[]={s.pose.position[1],s.vertical_speed,s.pose.pitch,s.pitch_velocity,s.pose.roll,s.roll_velocity,s.support_vertical_speed,(double)s.airborne,(double)s.left_flying,(double)s.right_flying,(double)s.grounded_once,s.pose.position[0],s.pose.position[2],s.speed,s.turn_speed,(double)s.support_rise_blocked,(double)s.pending_landing_impacts.size()};
 for(int i=0;i<17;++i)out[i]=values[i];
}
extern "C" WOT_PROBE_EXPORT void bot_ground(const double*x,const double*heights,unsigned mask,unsigned flags,double*out){
 AuthorityActor a{};a.id=1;a.kind=AuthorityActorKind::bot;a.condition.maximum_health=400;
 SuspensionParameters p;p.mass=10000;p.pitch_inertia=30000;p.roll_inertia=10000;p.fixed_step=1./60;p.constraint_iterations=10;
 for(int pair=0;pair<5;++pair)for(int side=0;side<2;++side){SuspensionSpring s;s.x=side?1:-1;s.z=pair-2;s.side=side?ContactSide::right:ContactSide::left;s.static_compression=.2;s.max_compression=.4;s.stiffness=p.mass*9.81*1.25/(10*.2);s.damping=2000;s.max_force=100000;p.springs.push_back(s);}
 a.suspension=p;a.state.pose.position={x[0],x[1],x[2]};a.state.pose.yaw=x[3];a.state.speed=x[4];a.state.turn_speed=x[5];a.state.pose.pitch=x[6];a.state.terrain_pitch=x[6];a.state.pose.roll=x[7];a.state.vertical_speed=x[8];a.state.pitch_velocity=x[9];a.state.roll_velocity=x[10];a.state.grounded_once=flags&1;a.state.airborne=flags&2;a.state.air_lateral={3,4};
 if(flags&4){GroundPlane old;old.center_x=0;old.center_z=0;old.normal={0,1,0};a.state.ground_plane=old;}
 AuthorityPose before;before.position={x[12],x[13],x[14]};AuthorityGroundSnapshot q;q.motion_pose=before;for(unsigned i=0;i<10;++i)q.springs.push_back(mask&(1<<i)?std::optional<double>(heights[i]):std::nullopt);
 if(flags&8)q.hidden_center_support=3;
 auto result=settle_authority_suspension(a,before,q,x[11],{});
 const auto&s=a.state;double values[]={s.pose.position[1],s.vertical_speed,s.pose.pitch,s.pitch_velocity,s.pose.roll,s.roll_velocity,s.support_vertical_speed,(double)s.airborne,(double)s.left_flying,(double)s.right_flying,(double)s.grounded_once,s.pose.position[0],s.pose.position[2],s.speed,s.turn_speed,(double)s.support_rise_blocked,(double)s.pending_landing_impacts.size()};
 for(int i=0;i<17;++i)out[i]=values[i];
}
